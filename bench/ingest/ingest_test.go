package stenoingest

// Ingest-side cost: what a backend or aggregator spends per record when it
// receives JSON lines, compared with what the expander spends on a stroke.
// Standard library only, so it builds without the zap module.

import (
	"bytes"
	"encoding/json"
	"io"
	"log/slog"
	"context"
	"strconv"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

const (
	msg    = "PacketResponder terminating"
	logger = "dfs.DataNode$PacketResponder"
	resp   = 0
	block  = int64(-6952295868487656571)
	pid    = 222
)

var _ = io.Discard
// ---- stroke encoder ----
var seq atomic.Uint64
var bufPool = sync.Pool{New: func() any { b := make([]byte, 0, 128); return &b }}

const (epoch = 'a'; tmplID = 0; loggerID = 0)

func escapeAppend(b []byte, s string) []byte {
	for i := 0; i < len(s); i++ {
		switch c := s[i]; c {
		case '\t': b = append(b, '\\', 't')
		case '\n': b = append(b, '\\', 'n')
		case '\\': b = append(b, '\\', '\\')
		default: b = append(b, c)
		}
	}
	return b
}

func stroke(w io.Writer, withTS bool, level string, tmpl, lg uint64, pidv int64, args ...int64) {
	bp := bufPool.Get().(*[]byte)
	b := (*bp)[:0]
	b = strconv.AppendUint(b, seq.Add(1), 36)
	if withTS { b = append(b, ' '); b = strconv.AppendInt(b, time.Now().UnixMicro(), 36) }
	b = append(b, ' ', epoch, ' ')
	b = escapeAppend(b, level)
	b = append(b, ' ')
	b = strconv.AppendInt(b, pidv, 10)
	b = append(b, ' ')
	b = strconv.AppendUint(b, lg, 36)
	b = append(b, ' ')
	b = strconv.AppendUint(b, tmpl, 36)
	for _, a := range args { b = append(b, '\t'); b = strconv.AppendInt(b, a, 10) }
	b = append(b, '\n')
	w.Write(b)
	*bp = b
	bufPool.Put(bp)
}

// ---- expander: stroke back to the JSON record the backend expects ----

type tmpl struct {
	msgParts []string // literal text around each argument
	logger   string
}

var dict = map[uint64]tmpl{0: {msgParts: []string{"PacketResponder ", " for block blk_", " terminating"}, logger: logger}}
var loggers = map[uint64]string{0: logger}

func unescapeAppend(dst []byte, s []byte) []byte {
	for i := 0; i < len(s); i++ {
		if s[i] == '\\' && i+1 < len(s) {
			i++
			switch s[i] {
			case 't':
				dst = append(dst, '\t')
			case 'n':
				dst = append(dst, '\n')
			default:
				dst = append(dst, s[i])
			}
			continue
		}
		dst = append(dst, s[i])
	}
	return dst
}

// expand parses "<seq> <epoch> <level> <pid> <logger-id> <tmpl-id>\t<arg>..." and
// appends a JSON object to dst.
func expand(dst, line []byte) ([]byte, bool) {
	head := line
	var args [][]byte
	if i := bytes.IndexByte(line, '\t'); i >= 0 {
		head = line[:i]
		args = bytes.Split(line[i+1:], []byte{'\t'})
	}
	f := bytes.Fields(head)
	if len(f) < 6 {
		return dst, false
	}
	tid, err := strconv.ParseUint(string(f[5]), 36, 64)
	if err != nil {
		return dst, false
	}
	t, ok := dict[tid]
	if !ok || len(args) != len(t.msgParts)-1 {
		return dst, false
	}
	gid, _ := strconv.ParseUint(string(f[4]), 36, 64)
	dst = append(dst, `{"seq":`...)
	seq, _ := strconv.ParseUint(string(f[0]), 36, 64)
	dst = strconv.AppendUint(dst, seq, 10)
	dst = append(dst, `,"level":`...)
	dst = strconv.AppendQuote(dst, string(f[2]))
	dst = append(dst, `,"pid":`...)
	dst = append(dst, f[3]...)
	dst = append(dst, `,"logger":`...)
	dst = strconv.AppendQuote(dst, loggers[gid])
	var m []byte
	for i, part := range t.msgParts {
		m = append(m, part...)
		if i < len(args) {
			m = unescapeAppend(m, args[i])
		}
	}
	dst = append(dst, `,"msg":`...)
	dst = strconv.AppendQuote(dst, string(m))
	dst = append(dst, '}', '\n')
	return dst, true
}


func slogLine() []byte {
	var buf bytes.Buffer
	l := slog.New(slog.NewJSONHandler(&buf, nil)).With("logger", logger)
	l.LogAttrs(context.Background(), slog.LevelInfo, msg, slog.Int("pid", pid), slog.Int("responder", resp), slog.Int64("block", block))
	return bytes.TrimRight(buf.Bytes(), "\n")
}

func strokeLine() []byte {
	var buf bytes.Buffer
	stroke(&buf, false, "INFO", tmplID, loggerID, pid, resp, block)
	return bytes.TrimRight(buf.Bytes(), "\n")
}

type rec struct {
	Time      string `json:"time"`
	Level     string `json:"level"`
	Msg       string `json:"msg"`
	Logger    string `json:"logger"`
	Pid       int    `json:"pid"`
	Responder int    `json:"responder"`
	Block     int64  `json:"block"`
}

func BenchmarkIngestJSONMap(b *testing.B) {
	line := slogLine()
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		var m map[string]any
		if err := json.Unmarshal(line, &m); err != nil {
			b.Fatal(err)
		}
	}
}

func BenchmarkIngestJSONStruct(b *testing.B) {
	line := slogLine()
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		var r rec
		if err := json.Unmarshal(line, &r); err != nil {
			b.Fatal(err)
		}
	}
}

func BenchmarkExpand(b *testing.B) {
	line := strokeLine()
	out := make([]byte, 0, 256)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		var ok bool
		if out, ok = expand(out[:0], line); !ok {
			b.Fatal("expand failed")
		}
	}
}

func BenchmarkExpandThenJSONMap(b *testing.B) {
	line := strokeLine()
	out := make([]byte, 0, 256)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		out, _ = expand(out[:0], line)
		var m map[string]any
		if err := json.Unmarshal(out, &m); err != nil {
			b.Fatal(err)
		}
	}
}

func TestLines(t *testing.T) {
	t.Logf("slog %d bytes: %s", len(slogLine()), slogLine())
	out, ok := expand(nil, strokeLine())
	if !ok {
		t.Fatal("expand")
	}
	t.Logf("expanded %d bytes: %s", len(out), out)
}
