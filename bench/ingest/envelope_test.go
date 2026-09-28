package stenoingest

// Memory held per unresolved envelope (Algorithm 2, table U). Each envelope keeps
// a copy of the stroke line plus the per-record metadata a shipper attaches:
// pod UID, namespace, container name, and arrival time. The image digest is the
// map key and is shared by all envelopes of one image.

import (
	"fmt"
	"runtime"
	"testing"
	"time"
)

type envelope struct {
	line      []byte
	podUID    string
	namespace string
	container string
	arrived   int64
}

func heap() uint64 {
	runtime.GC()
	var m runtime.MemStats
	runtime.ReadMemStats(&m)
	return m.HeapAlloc
}

func TestEnvelopeFootprint(t *testing.T) {
	const n = 1_000_000
	line := strokeLine()
	// pad to the pooled mean S2 stroke of 67 bytes (Table 2)
	for len(line) < 67 {
		line = append(line, 'x')
	}
	digest := "sha256:" + fmt.Sprintf("%064x", 12345)
	before := heap()
	U := map[string][]envelope{}
	for i := 0; i < n; i++ {
		l := make([]byte, len(line))
		copy(l, line)
		U[digest] = append(U[digest], envelope{
			line:      l,
			podUID:    fmt.Sprintf("%08x-1a2b-4c3d-8e9f-%012x", i%50, i%50),
			namespace: fmt.Sprintf("orders-%d", i%3),
			container: fmt.Sprintf("orders-api-%d", i%2),
			arrived:   time.Now().UnixNano(),
		})
	}
	after := heap()
	per := float64(after-before) / n
	t.Logf("envelopes=%d stroke_bytes=%d heap_per_envelope=%.1f B", n, len(line), per)
	fmt.Printf("ENVELOPE_BYTES %.1f\n", per)
	runtime.KeepAlive(U)
}
