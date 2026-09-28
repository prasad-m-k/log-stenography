package stenobench

import (
	"bytes"
	"context"
	"io"
	"log/slog"
	"os"
	"strconv"
	"testing"
)

// ---- per-event cost including one write system call to a pipe ----
// A container's stdout is a pipe or FIFO read by the runtime, so every
// unbuffered log call costs one write(2). A goroutine drains the read end.

func pipeSink(b *testing.B) (*os.File, func()) {
	r, w, err := os.Pipe()
	if err != nil {
		b.Fatal(err)
	}
	done := make(chan struct{})
	go func() { io.Copy(io.Discard, r); close(done) }()
	return w, func() { w.Close(); <-done; r.Close() }
}

func BenchmarkPipeSlogJSON(b *testing.B) {
	w, stop := pipeSink(b)
	defer stop()
	l := slog.New(slog.NewJSONHandler(w, nil)).With("logger", logger)
	ctx := context.Background()
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		l.LogAttrs(ctx, slog.LevelInfo, msg, slog.Int("pid", pid), slog.Int("responder", resp), slog.Int64("block", block))
	}
}

func BenchmarkPipeStrokeTS(b *testing.B) {
	w, stop := pipeSink(b)
	defer stop()
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		stroke(w, true, "INFO", tmplID, loggerID, pid, resp, block)
	}
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

func BenchmarkExpand(b *testing.B) {
	var buf bytes.Buffer
	stroke(&buf, false, "INFO", tmplID, loggerID, pid, resp, block)
	line := bytes.TrimRight(buf.Bytes(), "\n")
	out := make([]byte, 0, 256)
	var n int64
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		var ok bool
		out, ok = expand(out[:0], line)
		if !ok {
			b.Fatal("expand failed")
		}
		n += int64(len(out))
	}
	b.ReportMetric(float64(n)/float64(b.N), "B/record")
}

func TestExpandRoundTrip(t *testing.T) {
	var buf bytes.Buffer
	stroke(&buf, false, "INFO", tmplID, loggerID, pid, resp, block)
	out, ok := expand(nil, bytes.TrimRight(buf.Bytes(), "\n"))
	if !ok {
		t.Fatal("expand failed")
	}
	want := `"msg":"PacketResponder 0 for block blk_-6952295868487656571 terminating"`
	if !bytes.Contains(out, []byte(want)) {
		t.Fatalf("got %s", out)
	}
}
