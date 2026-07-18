package main

import (
	"strings"
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
		if got := docChanged(c.id, c.listing, snap, time.Time{}); got != c.want {
			t.Errorf("%s: docChanged=%v, want %v", c.name, got, c.want)
		}
	}
}

// With no snapshot docs map, --since alone must drive the doc-level skip: a doc
// whose /documents listing date is newer than the cutoff is fetched, otherwise
// it is skipped. This is the snapshot-less incremental mode the agent uses.
func TestDocChangedSinceCutoff(t *testing.T) {
	empty := Snapshot{Docs: map[string]string{}}
	since := tm("2026-06-04T10:00:00Z")
	cases := []struct {
		name, listing string
		since         time.Time
		want          bool
	}{
		{"newer than cutoff -> fetch", "2026-06-04T10:00:01Z", since, true},
		{"equal to cutoff -> skip", "2026-06-04T10:00:00Z", since, false},
		{"older than cutoff -> skip", "2026-06-01T00:00:00Z", since, false},
		{"no cutoff, no snapshot -> fetch", "2026-06-01T00:00:00Z", time.Time{}, true},
		{"empty listing date -> fetch", "", since, true},
	}
	for _, c := range cases {
		if got := docChanged("D", c.listing, empty, c.since); got != c.want {
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

// ---- backlinks ----

const (
	idA = "d86295ad-3736-9f6c-1575-3c5b95218e2f"
	idB = "50E6A542-6B07-D6C2-83F6-134C176356EB" // uppercase on purpose
)

func TestExtractLinks(t *testing.T) {
	md := "см. [Задача #задача](block://" + idA + ") и [База](block://" + idB + "), " +
		"дип-линк craftdocs://open?spaceId=abc&blockId=" + idA + " и голый block://" + idB
	refs := extractLinks(md)
	if len(refs) != 4 {
		t.Fatalf("want 4 refs, got %d: %+v", len(refs), refs)
	}
	if refs[0].Text != "Задача #задача" || refs[0].Target != idA {
		t.Errorf("ref0 = %+v", refs[0])
	}
	if refs[1].Target != strings.ToLower(idB) {
		t.Errorf("ref1 target not lowercased: %+v", refs[1])
	}
	// The two markdown-form matches must not be re-counted by the raw pass.
	if refs[2].Text != "" || refs[2].Target != idA {
		t.Errorf("ref2 = %+v", refs[2])
	}
	if refs[3].Text != "" || refs[3].Target != strings.ToLower(idB) {
		t.Errorf("ref3 = %+v", refs[3])
	}
	if got := extractLinks("нет ссылок, просто текст"); got != nil {
		t.Errorf("no-link markdown must yield nil, got %+v", got)
	}
}

func linkTree() Block {
	return blk("DOC", "page", "Проект X", "2026-06-01T00:00:00Z",
		blk("t1", "text", "шапка: [Этап 1](block://"+idA+")", "2026-06-01T00:00:00Z"),
		blk("PG", "page", "Вложенная страница", "2026-06-01T00:00:00Z",
			blk("t2", "text", "и снова [Этап 1](block://"+idA+") дважды [Этап 1](block://"+idA+")", "2026-06-01T00:00:00Z"),
			blk("t3", "text", "другая цель [База](block://"+idB+")", "2026-06-01T00:00:00Z"),
		),
	)
}

func TestCollectLinks(t *testing.T) {
	var recs []LinkRecord
	collectLinks(linkTree(), "DOC", "", nil, &recs)
	if len(recs) != 3 { // t2's duplicate collapses
		t.Fatalf("want 3 records, got %d: %+v", len(recs), recs)
	}
	if recs[0].Source != "t1" || recs[0].SourcePage != "DOC" || recs[0].Target != idA {
		t.Errorf("rec0 = %+v", recs[0])
	}
	if recs[1].Source != "t2" || recs[1].SourcePage != "PG" {
		t.Errorf("rec1 = %+v", recs[1])
	}
	if len(recs[1].Path) != 2 || recs[1].Path[1] != "Вложенная страница" {
		t.Errorf("rec1 path = %+v", recs[1].Path)
	}
	if recs[2].Source != "t3" || recs[2].Target != strings.ToLower(idB) {
		t.Errorf("rec2 = %+v", recs[2])
	}
}

// Collection items arrive in a separate "items" array with full nested
// bodies; links inside them must be indexed and attributed to the item.
func TestCollectLinksInsideCollectionItems(t *testing.T) {
	coll := blk("COLL", "collection", "Сказки", "")
	item := blk("ITEM", "collectionItem", "Чайничек и последнее тепло", "",
		blk("body", "text", "герой из [Личности Оли](block://"+idA+")", ""),
	)
	coll.Items = []Block{item}
	doc := blk("DOC", "page", "Архив", "", coll)

	var recs []LinkRecord
	collectLinks(doc, "DOC", "", nil, &recs)
	if len(recs) != 1 {
		t.Fatalf("want 1 record from item body, got %d: %+v", len(recs), recs)
	}
	r := recs[0]
	if r.Source != "body" || r.SourcePage != "ITEM" || r.Target != idA {
		t.Errorf("record = %+v", r)
	}
	if len(r.Path) != 2 || r.Path[1] != "Чайничек и последнее тепло" {
		t.Errorf("path = %+v", r.Path)
	}
}

func TestQueryBacklinksCaseInsensitive(t *testing.T) {
	var recs []LinkRecord
	collectLinks(linkTree(), "DOC", "", nil, &recs)
	idx := LinksIndex{Links: recs}
	hits := queryBacklinks(idx, strings.ToUpper(idA))
	if len(hits) != 2 {
		t.Fatalf("want 2 backlinks for idA, got %d: %+v", len(hits), hits)
	}
	if hits[0].Source != "t1" || hits[1].Source != "t2" {
		t.Errorf("hit order = %s, %s", hits[0].Source, hits[1].Source)
	}
}

// Unchanged docs carry their records without a fetch; deleted docs drop; docs
// outside a narrowed --docs set are carried but never marked as indexed anew.
func TestRefreshLinksIndexCarryAndDrop(t *testing.T) {
	prior := LinksIndex{
		Docs: map[string]string{"D1": "2026-06-01T00:00:00Z", "D2": "2026-06-01T00:00:00Z", "D3": "2026-06-01T00:00:00Z"},
		Links: []LinkRecord{
			{Source: "a", SourceDoc: "D1", Target: idA},
			{Source: "b", SourceDoc: "D2", Target: idA},
			{Source: "c", SourceDoc: "D3", Target: idA},
		},
	}
	listing := []Document{
		{ID: "D1", LastModifiedAt: "2026-06-01T00:00:00Z"},                  // unchanged -> carry
		{ID: "D2", LastModifiedAt: "2026-06-01T00:00:00Z", IsDeleted: true}, // deleted -> drop
		// D3 gone from listing entirely -> drop
	}
	fresh, scanned, skipped, errs := refreshLinksIndex(nil, prior, listing, nil, map[string]bool{})
	if scanned != 0 || skipped != 1 {
		t.Errorf("scanned=%d skipped=%d, want 0/1", scanned, skipped)
	}
	if len(errs) != 0 {
		t.Errorf("unexpected errors: %v", errs)
	}
	if len(fresh.Links) != 1 || fresh.Links[0].SourceDoc != "D1" {
		t.Errorf("links = %+v, want only D1's record", fresh.Links)
	}
	if _, ok := fresh.Docs["D2"]; ok {
		t.Error("deleted doc date must not persist")
	}
}

// A narrowed --docs run must not shrink coverage: out-of-set docs carry over,
// and a never-indexed out-of-set doc must NOT get a date recorded (a later full
// run still has to fetch it). IDs are matched case-insensitively.
func TestRefreshLinksIndexNarrowedRun(t *testing.T) {
	prior := LinksIndex{
		Docs:  map[string]string{"D1": "2026-06-01T00:00:00Z"},
		Links: []LinkRecord{{Source: "a", SourceDoc: "D1", Target: idA}},
	}
	listing := []Document{
		{ID: "D1", LastModifiedAt: "2026-06-01T00:00:00Z"}, // in set, unchanged -> skip
		{ID: "D9", LastModifiedAt: "2026-06-05T00:00:00Z"}, // out of set, never indexed
	}
	fresh, scanned, skipped, errs := refreshLinksIndex(nil, prior, listing, []string{"d1", "MISSING-DOC"}, map[string]bool{})
	if scanned != 0 || skipped != 1 {
		t.Errorf("scanned=%d skipped=%d, want 0/1", scanned, skipped)
	}
	if len(fresh.Links) != 1 {
		t.Errorf("links = %+v, want D1 carried", fresh.Links)
	}
	if _, ok := fresh.Docs["D9"]; ok {
		t.Error("never-indexed out-of-set doc must not get a date recorded")
	}
	found := false
	for _, e := range errs {
		if strings.Contains(e, "MISSING-DOC") {
			found = true
		}
	}
	if !found {
		t.Errorf("expected out-of-listing warning for MISSING-DOC, got %v", errs)
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
