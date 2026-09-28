package stenobench

import (
	"time"
	"context"
	"io"
	"log/slog"
	"strconv"
	"sync"
	"sync/atomic"
	"testing"

	"go.uber.org/zap"
	"go.uber.org/zap/zapcore"
)

// Event under test: HDFS "PacketResponder %d for block blk_%d terminating", pid 222, INFO,
// logger dfs.DataNode$PacketResponder.
const (
	msg    = "PacketResponder terminating"
	logger = "dfs.DataNode$PacketResponder"
	resp   = 0
	block  = int64(-6952295868487656571)
	pid    = 222
)

type counter struct{ w io.Writer; n int64 }
func (c *counter) Write(p []byte) (int, error) { c.n += int64(len(p)); return len(p), nil }

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

func BenchmarkStroke(b *testing.B) {
	c := &counter{w: io.Discard}
	b.ReportAllocs()
	for i := 0; i < b.N; i++ { stroke(c, false, "INFO", tmplID, loggerID, pid, resp, block) }
	b.ReportMetric(float64(c.n)/float64(b.N), "B/line")
}

func BenchmarkSlogJSON(b *testing.B) {
	c := &counter{w: io.Discard}
	l := slog.New(slog.NewJSONHandler(c, nil)).With("logger", logger)
	ctx := context.Background()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		l.LogAttrs(ctx, slog.LevelInfo, msg, slog.Int("pid", pid), slog.Int("responder", resp), slog.Int64("block", block))
	}
	b.ReportMetric(float64(c.n)/float64(b.N), "B/line")
}

func BenchmarkZapJSON(b *testing.B) {
	c := &counter{w: io.Discard}
	enc := zapcore.NewJSONEncoder(zap.NewProductionEncoderConfig())
	core := zapcore.NewCore(enc, zapcore.AddSync(c), zapcore.InfoLevel)
	l := zap.New(core).Named(logger)
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		l.Info(msg, zap.Int("pid", pid), zap.Int("responder", resp), zap.Int64("block", block))
	}
	b.ReportMetric(float64(c.n)/float64(b.N), "B/line")
}

func BenchmarkStrokeTS(b *testing.B) {
	c := &counter{w: io.Discard}
	b.ReportAllocs()
	for i := 0; i < b.N; i++ { stroke(c, true, "INFO", tmplID, loggerID, pid, resp, block) }
	b.ReportMetric(float64(c.n)/float64(b.N), "B/line")
}
