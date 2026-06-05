package main

import (
	"testing"
	"time"
)

func tm(s string) time.Time {
	t, err := parseTime(s)
	if err != nil {
		panic(err)
	}
	return t
}

// blk is a tiny helper to build Block trees in tests.
func blk(id, typ, md, modified string, children ...Block) Block {
	b := Block{ID: id, Type: typ, Markdown: md, Content: children}
	if modified != "" {
		b.Metadata = &Meta{LastModifiedAt: modified}
	}
	return b
}

// Reproduces the real "Старый домен CRM" bug: the document root is old, but a
// nested note page was edited later, and a task deep inside a stage page was
// edited later still. Root-only date detection misses both.
func crmTree() Block {
	return blk("DOC_CRM", "page", "Старый домен CRM 🤖", "2026-06-01T09:33:38.000+03:00",
		blk("b_callout", "text", "Получить 2+ на ревью", "2026-05-13T13:05:33.000+03:00"),
		blk("PG_GOAL", "page", "Цель проекта #заметка", "2026-05-19T13:35:58.000+03:00",
			blk("g1", "text", "Резюме", "2026-06-04T13:22:18.000+03:00"), // deep edit
		),
		blk("PG_STAGE1", "page", "Этап 1: Исследование", "2026-05-13T13:00:07.000+03:00",
			blk("t1", "text", "slug в GQL", "2026-06-03T11:07:23.000+03:00"), // task edit
		),
	)
}

func TestCollectPagesRollup(t *testing.T) {
	pages := map[string]*Page{}
	collectPages(crmTree(), "DOC_CRM", "", nil, map[string]bool{}, pages)

	if len(pages) != 3 {
		t.Fatalf("want 3 page units, got %d", len(pages))
	}
	cases := map[string]string{
		"DOC_CRM":   "2026-06-01T09:33:38+03:00", // root unchanged: non-page child older, nested pages excluded
		"PG_GOAL":   "2026-06-04T13:22:18+03:00", // rolled up from deep text edit
		"PG_STAGE1": "2026-06-03T11:07:23+03:00", // rolled up from task edit
	}
	for id, want := range cases {
		p, ok := pages[id]
		if !ok {
			t.Fatalf("missing page %s", id)
		}
		if !p.Eff.Equal(tm(want)) {
			t.Errorf("%s eff = %s, want %s", id, p.Eff.Format(time.RFC3339), want)
		}
	}
}

func TestExcludeStopsRecursion(t *testing.T) {
	pages := map[string]*Page{}
	collectPages(crmTree(), "DOC_CRM", "", nil, map[string]bool{"PG_GOAL": true}, pages)
	if _, ok := pages["PG_GOAL"]; ok {
		t.Error("excluded page should be absent")
	}
	if len(pages) != 2 {
		t.Errorf("want 2 pages after exclude, got %d", len(pages))
	}
}

func TestDiffCatchesNestedEdits(t *testing.T) {
	pages := map[string]*Page{}
	collectPages(crmTree(), "DOC_CRM", "", nil, map[string]bool{}, pages)

	// Simulate the old root-only (v6) snapshot: stored stale dates for every page.
	snap := Snapshot{Version: 6, Pages: map[string]string{
		"DOC_CRM":   "2026-06-01T09:33:38+03:00",
		"PG_GOAL":   "2026-05-19T13:35:58+03:00",
		"PG_STAGE1": "2026-05-13T13:00:07+03:00",
	}}
	res, fresh := Diff(pages, snap, time.Time{})

	if len(res.Changed) != 2 {
		t.Fatalf("want 2 changed, got %d: %+v", len(res.Changed), res.Changed)
	}
	// Sorted newest-first: PG_GOAL then PG_STAGE1.
	if res.Changed[0].ID != "PG_GOAL" || res.Changed[0].Status != "changed" {
		t.Errorf("first changed = %+v, want PG_GOAL/changed", res.Changed[0])
	}
	if res.Changed[1].ID != "PG_STAGE1" {
		t.Errorf("second changed = %+v, want PG_STAGE1", res.Changed[1])
	}
	if res.UnchangedCount != 1 { // DOC_CRM unchanged
		t.Errorf("unchanged = %d, want 1", res.UnchangedCount)
	}
	if fresh.Version != 7 {
		t.Errorf("fresh version = %d, want 7", fresh.Version)
	}
	if got := fresh.Pages["PG_GOAL"]; got == "" {
		t.Error("fresh snapshot missing PG_GOAL")
	}
}

func TestDiffEmptySnapshotAllNew(t *testing.T) {
	pages := map[string]*Page{}
	collectPages(crmTree(), "DOC_CRM", "", nil, map[string]bool{}, pages)
	res, _ := Diff(pages, Snapshot{Pages: map[string]string{}}, time.Time{})
	if len(res.Changed) != 3 {
		t.Fatalf("want 3 new, got %d", len(res.Changed))
	}
	for _, c := range res.Changed {
		if c.Status != "new" {
			t.Errorf("%s status = %s, want new", c.ID, c.Status)
		}
	}
}

func TestDiffReRunStable(t *testing.T) {
	pages := map[string]*Page{}
	collectPages(crmTree(), "DOC_CRM", "", nil, map[string]bool{}, pages)
	_, fresh := Diff(pages, Snapshot{Pages: map[string]string{}}, time.Time{})
	// Feeding the fresh snapshot back must yield zero changes.
	res2, _ := Diff(pages, fresh, time.Time{})
	if len(res2.Changed) != 0 {
		t.Errorf("re-run changed = %d, want 0: %+v", len(res2.Changed), res2.Changed)
	}
	if res2.UnchangedCount != 3 {
		t.Errorf("re-run unchanged = %d, want 3", res2.UnchangedCount)
	}
}

func TestDiffSinceFallback(t *testing.T) {
	pages := map[string]*Page{}
	collectPages(crmTree(), "DOC_CRM", "", nil, map[string]bool{}, pages)
	// No snapshot, but --since after PG_STAGE1/DOC_CRM and before PG_GOAL:
	// only PG_GOAL should count as new.
	res, _ := Diff(pages, Snapshot{Pages: map[string]string{}}, tm("2026-06-03T12:00:00+03:00"))
	if len(res.Changed) != 1 || res.Changed[0].ID != "PG_GOAL" {
		t.Fatalf("want only PG_GOAL new, got %+v", res.Changed)
	}
}

// Diff must record which root doc each page belongs to, so incremental runs can
// carry over the pages of skipped docs.
func TestDiffRecordsPageDoc(t *testing.T) {
	pages := map[string]*Page{}
	collectPages(crmTree(), "DOC_CRM", "", nil, map[string]bool{}, pages)
	_, fresh := Diff(pages, Snapshot{Pages: map[string]string{}}, time.Time{})
	for _, id := range []string{"DOC_CRM", "PG_GOAL", "PG_STAGE1"} {
		if fresh.PageDoc[id] != "DOC_CRM" {
			t.Errorf("PageDoc[%s] = %q, want DOC_CRM", id, fresh.PageDoc[id])
		}
	}
}

func TestDocChanged(t *testing.T) {
	snap := Snapshot{Docs: map[string]string{"D": "2026-06-04T10:00:00Z"}}
	cases := []struct {
		name, id, listing string
		want              bool
	}{
		{"newer listing -> fetch", "D", "2026-06-04T10:00:01Z", true},
		{"same listing -> skip", "D", "2026-06-04T10:00:00Z", false},
		{"older listing -> skip", "D", "2026-06-01T00:00:00Z", false},
		{"unknown doc -> fetch", "X", "2026-06-04T10:00:00Z", true},
		{"empty listing date -> fetch", "D", "", true},
	}
	for _, c := range cases {
		if got := docChanged(c.id, c.listing, snap); got != c.want {
			t.Errorf("%s: docChanged=%v, want %v", c.name, got, c.want)
		}
	}
}

func TestCarryOver(t *testing.T) {
	snap := Snapshot{
		Pages:   map[string]string{"p1": "2026-06-01T00:00:00Z", "p2": "2026-06-02T00:00:00Z", "q1": "2026-05-01T00:00:00Z"},
		Titles:  map[string]string{"p1": "P1", "p2": "P2", "q1": "Q1"},
		PageDoc: map[string]string{"p1": "DOC_A", "p2": "DOC_A", "q1": "DOC_B"},
	}
	pages := map[string]*Page{}
	n := carryOver("DOC_A", snap, pages)
	if n != 2 {
		t.Fatalf("carried %d, want 2", n)
	}
	if _, ok := pages["q1"]; ok {
		t.Error("q1 belongs to DOC_B, must not be carried for DOC_A")
	}
	if p := pages["p2"]; p == nil || p.RootDoc != "DOC_A" || !p.Eff.Equal(tm("2026-06-02T00:00:00Z")) {
		t.Errorf("p2 carried wrong: %+v", p)
	}
	// A carried (unchanged) page must diff as unchanged against the same snapshot.
	res, _ := Diff(pages, snap, time.Time{})
	if len(res.Changed) != 0 {
		t.Errorf("carried pages should be unchanged, got %+v", res.Changed)
	}
}

func TestBackoffSchedules(t *testing.T) {
	if d := transientBackoff(0); d != 2*time.Second {
		t.Errorf("transientBackoff(0)=%v, want 2s", d)
	}
	if d := transientBackoff(2); d != 8*time.Second {
		t.Errorf("transientBackoff(2)=%v, want 8s", d)
	}
	want := []time.Duration{5, 10, 20, 40, 60, 60}
	for n, w := range want {
		if got := rateLimitBackoff(n); got != time.Duration(w)*time.Second {
			t.Errorf("rateLimitBackoff(%d)=%v, want %ds", n, got, w)
		}
	}
}
