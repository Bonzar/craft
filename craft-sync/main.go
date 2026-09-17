// craft-sync — deterministic change detector for the Craft Multi-Document REST API.
//
// Purpose: do the heavy lifting (deep fetch of full block trees + date diffing)
// OUTSIDE the agent. The agent calls this binary with a snapshot of previously
// seen modification times; the binary fetches every block tree over REST, rolls
// modification times up to PAGE granularity (so edits inside nested pages are
// caught — the bug that root-only lastModifiedAt misses), diffs against the
// snapshot, and prints ONLY the changed/new page IDs. The giant JSON never
// touches the agent's context.
//
// Auth: the connect-link token is embedded in the base URL, so no extra header
// is required. Provide the base via --base or the CRAFT_API_BASE env var, e.g.
//
//	https://connect.craft.do/links/XXXXXXXX/api/v1
package main

import (
	"bytes"
	"compress/gzip"
	"context"
	"encoding/base64"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

// Block mirrors the GET /blocks response shape (only the fields we need).
// Collection blocks carry their items in a separate "items" array (type
// collectionItem), each with a full nested content tree — not in "content".
type Block struct {
	ID       string  `json:"id"`
	Type     string  `json:"type"`
	Markdown string  `json:"markdown"`
	RawCode  string  `json:"rawCode"`
	Metadata *Meta   `json:"metadata"`
	Content  []Block `json:"content"`
	Items    []Block `json:"items"`
}

type Meta struct {
	LastModifiedAt string `json:"lastModifiedAt"`
	CreatedAt      string `json:"createdAt"`
}

// Document mirrors a GET /documents item.
type Document struct {
	ID             string `json:"id"`
	Title          string `json:"title"`
	IsDeleted      bool   `json:"isDeleted"`
	LastModifiedAt string `json:"lastModifiedAt"`
}

type docsResponse struct {
	Items []Document `json:"items"`
}

// Page is one change-detection unit (a block of type "page").
type Page struct {
	ID       string    `json:"id"`
	Title    string    `json:"title"`
	ParentID string    `json:"parentId"`
	Path     []string  `json:"path"`
	RootDoc  string    `json:"-"` // root document this page was scanned under
	Eff      time.Time `json:"-"`
}

// Snapshot is the on-disk record of last-seen modification times per page.
//
// Docs/PageDoc enable incremental scans: the cheap GET /documents listing
// reports a doc-level lastModifiedAt that DOES roll up nested-page edits (unlike
// per-block metadata in GET /blocks), so a root doc whose listing date hasn't
// advanced past Docs[id] can be skipped entirely and its pages carried over
// from the previous snapshot — no deep fetch, no block budget spent.
type Snapshot struct {
	GeneratedAt string            `json:"generatedAt"`
	Version     int               `json:"version"`
	Docs        map[string]string `json:"docs,omitempty"`    // rootDocID -> doc-level lastModifiedAt (from /documents)
	Pages       map[string]string `json:"pages"`             // pageID -> RFC3339 lastModifiedAt
	Titles      map[string]string `json:"titles"`            // pageID -> title (for readability)
	PageDoc     map[string]string `json:"pageDoc,omitempty"` // pageID -> rootDocID (for carry-over of skipped docs)
}

type ChangedPage struct {
	ID             string   `json:"id"`
	Title          string   `json:"title"`
	Status         string   `json:"status"` // "new" | "changed"
	LastModifiedAt string   `json:"lastModifiedAt"`
	ParentID       string   `json:"parentId,omitempty"`
	Path           []string `json:"path,omitempty"`
}

type Result struct {
	Changed        []ChangedPage `json:"changed"`
	UnchangedCount int           `json:"unchangedCount"`
	TotalPages     int           `json:"totalPages"`
	ScannedDocs    int           `json:"scannedDocs"`
	SkippedDocs    int           `json:"skippedDocs"`
	Errors         []string      `json:"errors,omitempty"`
}

// ---- backlinks: link index over block:// references ----
//
// Craft has no native backlinks API: neither the connect-link REST API nor the
// MCP server expose incoming links, and the search index covers visible text
// only — a block-link's target ID lives in a text attribute and is unreachable
// by any search (verified empirically, including RE2 regex search by ID).
// Outgoing links, however, are fully present in each block's markdown as
// [text](block://<id>), so an inverted index over a full crawl answers
// "who links to X" exactly. This mode reuses the same doc-level incremental
// machinery as change detection: only docs whose /documents listing date has
// advanced are re-fetched; everything else is carried over from the index file.

// LinkRecord is one outgoing block-link occurrence: block Source (inside
// SourcePage of doc SourceDoc) links to block Target.
type LinkRecord struct {
	Source     string   `json:"source"`               // linking block ID
	SourceDoc  string   `json:"sourceDoc"`            // root document the source lives in
	SourcePage string   `json:"sourcePage,omitempty"` // nearest enclosing page block
	Path       []string `json:"path,omitempty"`       // page-title chain down to the source
	Target     string   `json:"target"`               // linked-to block ID (lowercased)
	Text       string   `json:"text,omitempty"`       // link text, when the markdown form carries one
}

// LinksIndex is the on-disk inverted-index source: every outgoing link in the
// space plus the doc-level dates that drive incremental refresh.
type LinksIndex struct {
	GeneratedAt string            `json:"generatedAt"`
	Docs        map[string]string `json:"docs"`  // rootDocID -> /documents lastModifiedAt at index time
	Links       []LinkRecord      `json:"links"` // every outgoing block link, flat
}

// BacklinksResult is the query answer printed in --backlinks mode.
type BacklinksResult struct {
	Target      string       `json:"target"`
	Count       int          `json:"count"`
	Backlinks   []LinkRecord `json:"backlinks"`
	TotalLinks  int          `json:"totalLinks"`      // size of the whole index
	ScannedDocs int          `json:"scannedDocs"`     // deep-fetched this run
	SkippedDocs int          `json:"skippedDocs"`     // date unchanged -> carried from index
	Stale       bool         `json:"stale,omitempty"` // answered from index without refresh
	IndexedAt   string       `json:"indexedAt,omitempty"`
	Errors      []string     `json:"errors,omitempty"`
}

func main() {
	var (
		base        = flag.String("base", os.Getenv("CRAFT_API_BASE"), "Base URL of the Craft connect-link API (or env CRAFT_API_BASE)")
		docsArg     = flag.String("docs", "", "Comma-separated root document IDs to scan. If empty, GET /documents and scan all non-deleted.")
		excludeArg  = flag.String("exclude", "", "Comma-separated document/page IDs to skip entirely (e.g. the 'Память для агента' infra docs).")
		snapPath    = flag.String("snapshot", "", "Path to snapshot JSON. Read for diffing; written back when --update-snapshot is set.")
		updateSnap  = flag.Bool("update-snapshot", false, "Write the freshly computed snapshot back to --snapshot after diffing.")
		sinceArg    = flag.String("since", "", "RFC3339 fallback: when a page is absent from the snapshot, treat it as changed only if newer than this. Empty => absent pages are 'new'.")
		tree        = flag.Bool("tree", false, "Print changed pages as a nested tree instead of a flat list.")
		incremental = flag.Bool("incremental", false, "Skip deep-fetching root docs whose /documents listing date hasn't advanced past the snapshot's Docs[id] — or, when there is no snapshot, past --since. Only changed docs are deep-fetched; the rest cost just the cheap /documents listing. Cuts block-budget usage.")
		timeoutSec  = flag.Int("timeout", 60, "Per-request HTTP timeout in seconds.")
		retries     = flag.Int("retries", 3, "Retry attempts for transient failures (network / HTTP 5xx).")
		rlRetries   = flag.Int("rl-retries", 5, "Retry attempts for HTTP 429 'Block budget exceeded', with longer exponential backoff (5,10,20,40,60s capped).")

		backlinksTo  = flag.String("backlinks", "", "Backlinks mode: print every block whose markdown links to this block ID, then exit. Uses/refreshes --links-file or --links-store when given.")
		linksPath    = flag.String("links-file", "", "Path to the persistent link-index JSON for --backlinks/--links-refresh. Enables incremental refresh: docs whose /documents date hasn't advanced are carried over, not re-fetched.")
		linksStore   = flag.String("links-store", os.Getenv("CRAFT_LINKS_STORE"), "Craft page block ID holding the persistent link index (gzip+base64 dump in code blocks plus an 'Обновлён:' cutoff line); defaults to env CRAFT_LINKS_STORE. Cross-session alternative to --links-file; the store doc itself is excluded from indexing. Pass an empty value to force --links-file/local mode when the env var is set.")
		linksRefresh = flag.Bool("links-refresh", false, "Refresh the link index (see --links-file/--links-store) without querying a target. Lets a routine keep the index warm.")
		offline      = flag.Bool("offline", false, "With --backlinks: answer from --links-file/--links-store as-is, no refresh of the space.")

		markdownArg = flag.String("markdown", "", "Markdown mode: deep-fetch these comma-separated block IDs and print their markdown, then exit. Feeds the start-context provider.")
		followLinks = flag.Bool("follow-links", false, "With --markdown: also read every block linked from a «Связи» section, recursively, each ID at most once.")
		container   = flag.String("container", "", "With --markdown: deep-read this page once and serve the walk from its tree, fetching one by one only what it does not hold. Same output, far fewer requests when the walked pages live under one page.")
	)
	flag.Parse()

	if *base == "" && !(*offline && *linksPath != "") {
		fail("missing --base (or CRAFT_API_BASE): the Craft connect-link API base URL")
	}
	*base = strings.TrimRight(*base, "/")
	if *linksPath != "" && *linksStore != "" {
		storeExplicit := false
		flag.Visit(func(f *flag.Flag) {
			if f.Name == "links-store" {
				storeExplicit = true
			}
		})
		if storeExplicit {
			fail("--links-file and --links-store are mutually exclusive")
		}
		*linksStore = "" // env-default store yields to an explicit --links-file
	}

	exclude := toSet(*excludeArg)
	var since time.Time
	if *sinceArg != "" {
		t, err := parseTime(*sinceArg)
		if err != nil {
			fail("invalid --since: %v", err)
		}
		since = t
	}

	snap := Snapshot{Pages: map[string]string{}, Titles: map[string]string{}, Docs: map[string]string{}, PageDoc: map[string]string{}}
	if *snapPath != "" {
		if b, err := os.ReadFile(*snapPath); err == nil {
			if err := json.Unmarshal(b, &snap); err != nil {
				fail("cannot parse snapshot %s: %v", *snapPath, err)
			}
			if snap.Pages == nil {
				snap.Pages = map[string]string{}
			}
			if snap.Titles == nil {
				snap.Titles = map[string]string{}
			}
			if snap.Docs == nil {
				snap.Docs = map[string]string{}
			}
			if snap.PageDoc == nil {
				snap.PageDoc = map[string]string{}
			}
		}
	}

	client := &Client{
		http:      &http.Client{Timeout: time.Duration(*timeoutSec) * time.Second},
		base:      *base,
		retries:   *retries,
		rlRetries: *rlRetries,
	}

	if *markdownArg != "" {
		runMarkdown(client, *markdownArg, *followLinks, exclude, *container)
		return
	}

	if *backlinksTo != "" || *linksRefresh {
		runBacklinks(client, *backlinksTo, *docsArg, *excludeArg, *linksPath, *linksStore, *offline)
		return
	}

	var scanErrors []string

	// The /documents listing is cheap (metadata only, no block budget) and its
	// doc-level lastModifiedAt rolls up nested-page edits. We use it both to
	// resolve the scan set (when --docs is empty) and to drive incremental skip
	// and the fresh Docs map. Fetch it whenever we need the scan set, want
	// incremental skipping, or are writing a snapshot worth a Docs map.
	var listing []Document
	docDates := map[string]string{}
	needListing := *docsArg == "" || *incremental || (*updateSnap && *snapPath != "")
	if needListing {
		docs, err := client.listDocuments()
		if err != nil {
			if *docsArg == "" {
				fail("GET /documents failed: %v", err) // needed to know what to scan
			}
			scanErrors = append(scanErrors, fmt.Sprintf("listing (for incremental/docs map): %v", err))
			if *incremental {
				*incremental = false // can't skip safely without dates
			}
		}
		listing = docs
		for _, d := range docs {
			docDates[d.ID] = d.LastModifiedAt
		}
	}

	// Resolve the set of root documents to scan.
	var rootIDs []string
	if *docsArg != "" {
		rootIDs = splitCSV(*docsArg)
	} else {
		for _, d := range listing {
			if d.IsDeleted || exclude[d.ID] {
				continue
			}
			rootIDs = append(rootIDs, d.ID)
		}
	}

	pages := map[string]*Page{}
	scannedDocs := 0
	skippedDocs := 0

	for _, id := range rootIDs {
		if exclude[id] {
			continue
		}
		// Incremental skip: doc-level date hasn't advanced -> carry pages over.
		if *incremental && !docChanged(id, docDates[id], snap, since) {
			carryOver(id, snap, pages)
			skippedDocs++
			continue
		}
		root, err := client.getBlocks(id)
		if err != nil {
			scanErrors = append(scanErrors, fmt.Sprintf("%s: %v", id, err))
			continue // skip ghost/broken docs without dying
		}
		scannedDocs++
		collectPages(root, id, "", nil, exclude, pages)
	}

	res, fresh := Diff(pages, snap, since)
	res.ScannedDocs = scannedDocs
	res.SkippedDocs = skippedDocs
	res.Errors = append(res.Errors, scanErrors...)

	// Record doc-level dates for every root we resolved, so the next run can skip
	// unchanged docs. Prefer the fresh listing date; fall back to the prior value.
	for _, id := range rootIDs {
		if d := docDates[id]; d != "" {
			fresh.Docs[id] = d
		} else if d, ok := snap.Docs[id]; ok {
			fresh.Docs[id] = d
		}
	}

	if *updateSnap && *snapPath != "" {
		out, _ := json.MarshalIndent(fresh, "", "  ")
		if err := os.WriteFile(*snapPath, out, 0o644); err != nil {
			fail("cannot write snapshot %s: %v", *snapPath, err)
		}
	}

	if *tree {
		printTree(res, pages)
	} else {
		out, _ := json.MarshalIndent(res, "", "  ")
		fmt.Println(string(out))
	}
}

// Diff compares the freshly scanned pages against the previous snapshot and
// returns the change Result plus the freshly computed snapshot to persist.
// A page is "new" if absent from the snapshot (and, when since is set, newer
// than it), "changed" if its effective time is after the snapshot's value.
func Diff(pages map[string]*Page, snap Snapshot, since time.Time) (Result, Snapshot) {
	res := Result{}
	fresh := Snapshot{
		GeneratedAt: time.Now().UTC().Format(time.RFC3339),
		Version:     snap.Version + 1,
		Docs:        map[string]string{},
		Pages:       map[string]string{},
		Titles:      map[string]string{},
		PageDoc:     map[string]string{},
	}
	for id, p := range pages {
		fresh.Pages[id] = p.Eff.Format(time.RFC3339Nano)
		fresh.Titles[id] = p.Title
		if p.RootDoc != "" {
			fresh.PageDoc[id] = p.RootDoc
		}
	}
	res.TotalPages = len(pages)

	for id, p := range pages {
		prevStr, seen := snap.Pages[id]
		if !seen {
			if !since.IsZero() && !p.Eff.After(since) {
				res.UnchangedCount++
				continue
			}
			res.Changed = append(res.Changed, mkChanged(p, "new"))
			continue
		}
		prev, err := parseTime(prevStr)
		if err != nil || p.Eff.After(prev) {
			res.Changed = append(res.Changed, mkChanged(p, "changed"))
		} else {
			res.UnchangedCount++
		}
	}
	sort.Slice(res.Changed, func(i, j int) bool {
		return res.Changed[i].LastModifiedAt > res.Changed[j].LastModifiedAt
	})
	return res, fresh
}

// collectPages walks a block tree, registering every page-type block as a unit
// whose effective time is the max lastModifiedAt of itself and all descendants
// that are NOT inside a deeper page (those are their own units). This rolls
// non-page edits up to their containing page while attributing nested-page
// edits to the nested page — catching edits the root-only date would miss.
func collectPages(b Block, rootDoc, encPageID string, encPath []string, exclude map[string]bool, pages map[string]*Page) (nonPageMax time.Time) {
	if exclude[b.ID] {
		return time.Time{}
	}
	if b.Type == "page" {
		title := firstLine(b.Markdown)
		path := append(append([]string{}, encPath...), title)
		eff := blockTime(b)
		for _, c := range b.Content {
			eff = maxT(eff, collectPages(c, rootDoc, b.ID, path, exclude, pages))
		}
		pages[b.ID] = &Page{ID: b.ID, Title: title, ParentID: encPageID, Path: encPath, RootDoc: rootDoc, Eff: eff}
		return time.Time{} // a page does not contribute to its enclosing page's non-page max
	}
	m := blockTime(b)
	for _, c := range b.Content {
		m = maxT(m, collectPages(c, rootDoc, encPageID, encPath, exclude, pages))
	}
	return m
}

// docChanged reports whether a root doc must be deep-fetched. The /documents
// listing date rolls up nested-page edits at any depth (verified empirically: a
// doc whose only recent edit lived in a deeply nested page still advances its
// listing date, while its own root-block date does not), so it is a safe
// doc-level gate against the cross-page bug that plain root-block dates suffer.
//
// Decision order:
//   - snapshot has a prior doc date    -> fetch iff the listing date is newer;
//   - no snapshot entry but --since set -> fetch iff the listing date is newer
//     than since (lets a single stored cutoff drive incremental skipping with no
//     docs map at all);
//   - otherwise (no basis to skip)      -> fetch.
//
// An empty or unparseable listing date always means fetch: we never skip a doc
// we cannot date.
func docChanged(rootID, listingDate string, snap Snapshot, since time.Time) bool {
	lt, lerr := parseTime(listingDate)
	if lerr != nil {
		return true
	}
	if prev, ok := snap.Docs[rootID]; ok {
		pt, perr := parseTime(prev)
		if perr != nil {
			return true
		}
		return lt.After(pt)
	}
	if !since.IsZero() {
		return lt.After(since)
	}
	return true
}

// carryOver copies a skipped doc's pages from the previous snapshot into the
// working set unchanged, so they survive into the fresh snapshot and are not
// re-reported. Returns the number of pages carried.
func carryOver(rootID string, snap Snapshot, pages map[string]*Page) int {
	n := 0
	for pid, doc := range snap.PageDoc {
		if doc != rootID {
			continue
		}
		t, _ := parseTime(snap.Pages[pid])
		pages[pid] = &Page{ID: pid, Title: snap.Titles[pid], RootDoc: rootID, Eff: t}
		n++
	}
	return n
}

func mkChanged(p *Page, status string) ChangedPage {
	return ChangedPage{
		ID:             p.ID,
		Title:          p.Title,
		Status:         status,
		LastModifiedAt: p.Eff.Format(time.RFC3339Nano),
		ParentID:       p.ParentID,
		Path:           p.Path,
	}
}

// printTree renders changed pages nested under changed ancestors when possible.
func printTree(res Result, pages map[string]*Page) {
	changed := map[string]ChangedPage{}
	for _, c := range res.Changed {
		changed[c.ID] = c
	}
	children := map[string][]string{}
	var roots []string
	for _, c := range res.Changed {
		if _, parentChanged := changed[c.ParentID]; parentChanged {
			children[c.ParentID] = append(children[c.ParentID], c.ID)
		} else {
			roots = append(roots, c.ID)
		}
	}
	var walk func(id string, depth int)
	walk = func(id string, depth int) {
		c := changed[id]
		fmt.Printf("%s- %s [%s] %s (%s)\n", strings.Repeat("  ", depth), c.Title, c.Status, c.LastModifiedAt, c.ID)
		for _, ch := range children[id] {
			walk(ch, depth+1)
		}
	}
	fmt.Printf("Changed pages: %d  |  unchanged: %d  |  total: %d  |  scanned: %d  |  skipped: %d\n",
		len(res.Changed), res.UnchangedCount, res.TotalPages, res.ScannedDocs, res.SkippedDocs)
	for _, id := range roots {
		walk(id, 0)
	}
	for _, e := range res.Errors {
		fmt.Printf("! error: %s\n", e)
	}
}

// ---- markdown mode: read a block subtree for the agent's start context ----
//
// The start-context provider needs the TEXT of a few known sections, not a
// change list: the base rules live under «Сущность базы #раздел» and are
// reached by following the links its «Связи» section lists, and the agent's
// memory is one page read whole. Every block already carries its own markdown
// in the same GET /blocks tree the change detector walks, so this mode is a
// render of that tree plus an optional walk over the links under a named
// section. Output is markdown on stdout — the hook feeds it to the model as is.

// linkSection is the section heading whose links --follow-links walks.
const linkSection = "Связи"

var headingRe = regexp.MustCompile(`^\s{0,3}#{1,6}\s+`)

// renderMarkdown appends the markdown of every block in the tree, in document
// order. Code blocks carry their body in rawCode rather than markdown.
func renderMarkdown(b Block, out *strings.Builder) {
	if md := strings.TrimRight(b.Markdown, " \t"); md != "" {
		out.WriteString(md)
		out.WriteString("\n\n")
	} else if b.RawCode != "" {
		out.WriteString("```\n")
		out.WriteString(strings.TrimRight(b.RawCode, "\n"))
		out.WriteString("\n```\n\n")
	}
	for _, child := range b.Content {
		renderMarkdown(child, out)
	}
	for _, item := range b.Items {
		renderMarkdown(item, out)
	}
}

// linksUnder collects every block link that sits under a heading whose text
// contains `section`. The heading in force is the nearest preceding heading in
// document order, so a «Связи» list is picked up while links in the body of the
// page are not.
func linksUnder(b Block, section string, heading *string, out *[]string) {
	md := b.Markdown
	if headingRe.MatchString(md) {
		*heading = md
	} else if strings.Contains(*heading, section) {
		for _, ref := range extractLinks(md) {
			// Именно Raw: спрашивать блок надо тем регистром, каким он записан.
			*out = append(*out, ref.Raw)
		}
	}
	for _, child := range b.Content {
		linksUnder(child, section, heading, out)
	}
	for _, item := range b.Items {
		linksUnder(item, section, heading, out)
	}
}

// markdownWorkers bounds how many block trees are fetched at once. The walk is
// latency-bound, not CPU-bound: one GET /blocks costs ~0.5 s and the API takes
// one id per call (comma-separated ids answer 404, a repeated id parameter 400
// — checked 17.09.2026), so the only way to shorten the walk is to overlap the
// requests. Six is a compromise: it collapses a wide level into one round trip
// without pushing the connect-link's block budget into HTTP 429. Twelve was
// measured against it on the live space (17.09.2026): 3.3-4.1 s against 3.7 s,
// i.e. inside the spread of the runs themselves, so the narrower pool keeps the
// win and leaves the rate limit alone.
const markdownWorkers = 6

// fetched is one block tree of a level, kept with its position so that the
// output order stays the discovery order and not the order of completion.
type fetched struct {
	id    string
	block Block
	err   error
}

// indexTree maps every block of a tree by its lowercased id, so that a page
// the walk asks for can be served from a tree already in hand.
func indexTree(b Block, into map[string]Block) {
	if b.ID != "" {
		if _, seen := into[strings.ToLower(b.ID)]; !seen {
			into[strings.ToLower(b.ID)] = b
		}
	}
	for _, child := range b.Content {
		indexTree(child, into)
	}
	for _, item := range b.Items {
		indexTree(item, into)
	}
}

// runMarkdown prints the markdown of each requested block tree, optionally
// following the links listed in its «Связи» section. Every ID is fetched at
// most once; a failed fetch is reported on stderr and does not stop the rest —
// a partial start context beats no start context.
//
// The walk goes level by level and fetches each level in parallel: «Связи»
// fans out wide and shallow (23 blocks over 2 levels on the base rules,
// 17.09.2026), so a sequential walk pays the API's latency once per block for
// no reason. Rendering stays sequential over the level in its discovery order,
// so the output does not depend on which request finished first.
//
// With --container the walk first reads one page whole and then serves itself
// from that tree. GET /blocks?maxDepth=-1 crosses page boundaries — a nested
// page comes back with its own content, not as a stub (checked 17.09.2026) —
// so when the walked pages live under one page, that single read replaces all
// of them. The walk itself is unchanged: the same «Связи» decide what is read
// and in what order, the container only answers instead of the API. Whatever
// the container does not hold is still fetched by id, so a page moved out of
// it costs one request, not a wrong answer. A container that fails to read is
// a note on stderr and a plain walk, not a failure: the text is the point.
func runMarkdown(client *Client, idsArg string, follow bool, exclude map[string]bool, container string) {
	// Сравниваем id без учёта регистра, спрашиваем — в исходном.
	skip := map[string]bool{}
	for id := range exclude {
		skip[strings.ToLower(id)] = true
	}
	seen := map[string]bool{}
	var level []string
	for _, id := range splitCSV(idsArg) {
		if key := strings.ToLower(id); !seen[key] && !skip[key] {
			seen[key] = true
			level = append(level, id)
		}
	}

	// Дерево контейнера, если его попросили: отсюда уровень берётся даром.
	have := map[string]Block{}
	if container != "" {
		block, err := client.getBlocks(container)
		if err != nil {
			fmt.Fprintf(os.Stderr, "! markdown: контейнер %s: %v (читаем обходом)\n", container, err)
		} else {
			indexTree(block, have)
		}
	}

	var out strings.Builder
	var failures []string
	for len(level) > 0 {
		results := make([]fetched, len(level))
		var wg sync.WaitGroup
		gate := make(chan struct{}, markdownWorkers)
		for i, id := range level {
			if block, ok := have[strings.ToLower(id)]; ok {
				results[i] = fetched{id: id, block: block}
				continue
			}
			wg.Add(1)
			go func(i int, id string) {
				defer wg.Done()
				gate <- struct{}{}
				defer func() { <-gate }()
				block, err := client.getBlocks(id)
				results[i] = fetched{id: id, block: block, err: err}
			}(i, id)
		}
		wg.Wait()

		var next []string
		for _, result := range results {
			if result.err != nil {
				failures = append(failures, fmt.Sprintf("%s: %v", result.id, result.err))
				continue
			}
			renderMarkdown(result.block, &out)
			if !follow {
				continue
			}
			heading := ""
			var found []string
			linksUnder(result.block, linkSection, &heading, &found)
			for _, id := range found {
				if key := strings.ToLower(id); !seen[key] && !skip[key] {
					seen[key] = true
					next = append(next, id)
				}
			}
		}
		level = next
	}

	fmt.Print(strings.TrimRight(out.String(), "\n") + "\n")
	for _, f := range failures {
		fmt.Fprintf(os.Stderr, "! markdown: %s\n", f)
	}
	if len(failures) == len(seen) && len(seen) > 0 {
		os.Exit(1) // nothing was read at all: the caller must not mistake it for an empty space
	}
}

// ---- backlinks mode ----

// Block IDs are UUIDs; Craft serializes block links into markdown two ways:
// the native form [text](block://<id>) and deep links craftdocs://open?...&blockId=<id>
// (normalized to block:// on write, but tolerated on read just in case).
const uuidPat = `[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}`

var (
	mdBlockLinkRe = regexp.MustCompile(`\[([^\]]*)\]\(block://(` + uuidPat + `)\)`)
	rawBlockIDRe  = regexp.MustCompile(`(?:block://|[?&]blockId=)(` + uuidPat + `)`)
)

// LinkRef is one parsed outgoing reference inside a single markdown string.
//
// Target is lowercased because the backlinks index matches ids, and Craft
// writes the same id in different cases. Raw keeps the id exactly as the link
// spells it, because the connect-API is case-sensitive when ASKED for a block:
// an id lowercased on the way to GET /blocks answers 404 even though the page
// exists (checked 17.09.2026 on 3430452A-8643-4ECD-B414-A5E8BF8AC3F4). So:
// compare by Target, request by Raw.
type LinkRef struct {
	Text   string
	Target string // lowercased, for matching
	Raw    string // as written, for asking the API
}

// extractLinks parses every block reference out of one block's markdown:
// first the [text](block://id) form (capturing the link text), then any
// leftover bare block:// or blockId= occurrence on the remainder.
func extractLinks(md string) []LinkRef {
	if !strings.Contains(md, "block://") && !strings.Contains(md, "blockId=") {
		return nil
	}
	var out []LinkRef
	for _, m := range mdBlockLinkRe.FindAllStringSubmatch(md, -1) {
		out = append(out, LinkRef{Text: m[1], Target: strings.ToLower(m[2]), Raw: m[2]})
	}
	rest := mdBlockLinkRe.ReplaceAllString(md, "")
	for _, m := range rawBlockIDRe.FindAllStringSubmatch(rest, -1) {
		out = append(out, LinkRef{Target: strings.ToLower(m[1]), Raw: m[1]})
	}
	return out
}

// collectLinks walks a block tree and appends a LinkRecord for every outgoing
// block reference, attributed to its nearest enclosing page (same page notion
// as collectPages). Duplicate (target,text) pairs within one block collapse.
func collectLinks(b Block, rootDoc, encPage string, encPath []string, out *[]LinkRecord) {
	page, path := encPage, encPath
	// A collection item is a page-like unit (its markdown is the item title),
	// so backlinks from inside an item attribute to the item, not the doc page.
	if b.Type == "page" || b.Type == "collectionItem" {
		path = append(append([]string{}, encPath...), firstLine(b.Markdown))
		page = b.ID
	}
	if refs := extractLinks(b.Markdown); len(refs) > 0 {
		seen := map[string]bool{}
		for _, r := range refs {
			key := r.Target + "|" + r.Text
			if seen[key] {
				continue
			}
			seen[key] = true
			*out = append(*out, LinkRecord{
				Source: b.ID, SourceDoc: rootDoc, SourcePage: page,
				Path: path, Target: r.Target, Text: r.Text,
			})
		}
	}
	for _, c := range b.Content {
		collectLinks(c, rootDoc, page, path, out)
	}
	for _, it := range b.Items {
		collectLinks(it, rootDoc, page, path, out)
	}
}

// refreshLinksIndex brings the link index up to date against the live space:
// docs whose /documents listing date advanced past the index's recorded date
// are deep-fetched and their records rebuilt; unchanged docs carry over as-is;
// docs that disappeared from the listing (deleted) drop out. rootIDs narrows
// which docs are ELIGIBLE for re-fetching (empty = all live docs) — records of
// out-of-set docs are never dropped, so a narrowed run cannot shrink coverage.
func refreshLinksIndex(client *Client, prior LinksIndex, listing []Document, rootIDs []string, exclude map[string]bool) (LinksIndex, int, int, []string) {
	fresh := LinksIndex{
		GeneratedAt: time.Now().UTC().Format(time.RFC3339),
		Docs:        map[string]string{},
	}
	byDoc := map[string][]LinkRecord{}
	for _, r := range prior.Links {
		byDoc[r.SourceDoc] = append(byDoc[r.SourceDoc], r)
	}
	// Craft IDs are case-insensitive UUIDs and arrive in mixed case depending on
	// the source (listing vs MCP) — compare them lowercased.
	inScan := map[string]bool{}
	for _, id := range rootIDs {
		inScan[strings.ToLower(id)] = true
	}
	scanned, skipped := 0, 0
	var errs []string

	for _, d := range listing {
		if d.IsDeleted || exclude[d.ID] {
			continue // records dropped: deleted or explicitly excluded
		}
		eligible := len(rootIDs) == 0 || inScan[strings.ToLower(d.ID)]
		if !eligible || !linksDocChanged(d.ID, d.LastModifiedAt, prior) {
			fresh.Links = append(fresh.Links, byDoc[d.ID]...)
			// Carry only a date we actually indexed under: recording the current
			// listing date for a never-fetched doc would make future runs skip it.
			if prev, ok := prior.Docs[d.ID]; ok {
				fresh.Docs[d.ID] = prev
			}
			if eligible {
				skipped++
			}
			continue
		}
		root, err := client.getBlocks(d.ID)
		if err != nil {
			// Keep the stale records and the OLD date so the next run retries.
			errs = append(errs, fmt.Sprintf("%s: %v", d.ID, err))
			fresh.Links = append(fresh.Links, byDoc[d.ID]...)
			if prev, ok := prior.Docs[d.ID]; ok {
				fresh.Docs[d.ID] = prev
			}
			continue
		}
		scanned++
		collectLinks(root, d.ID, "", nil, &fresh.Links)
		if d.LastModifiedAt != "" {
			fresh.Docs[d.ID] = d.LastModifiedAt
		}
	}
	// A --docs ID absent from the listing is out of the connect-link scope (or
	// mistyped) and was silently unreachable — say so instead of hiding it.
	seenInListing := map[string]bool{}
	for _, d := range listing {
		seenInListing[strings.ToLower(d.ID)] = true
	}
	for _, id := range rootIDs {
		if !seenInListing[strings.ToLower(id)] {
			errs = append(errs, fmt.Sprintf("%s: not in /documents listing (out of connect-link scope?)", id))
		}
	}
	return fresh, scanned, skipped, errs
}

// linksDocChanged mirrors docChanged for the links index: re-fetch unless the
// listing date is present, parseable, and not newer than the recorded one.
func linksDocChanged(id, listingDate string, idx LinksIndex) bool {
	lt, err := parseTime(listingDate)
	if err != nil {
		return true
	}
	prev, ok := idx.Docs[id]
	if !ok {
		return true
	}
	pt, err := parseTime(prev)
	if err != nil {
		return true
	}
	return lt.After(pt)
}

// queryBacklinks filters the index down to records pointing at target.
func queryBacklinks(idx LinksIndex, target string) []LinkRecord {
	target = strings.ToLower(target)
	var hits []LinkRecord
	for _, r := range idx.Links {
		if r.Target == target {
			hits = append(hits, r)
		}
	}
	sort.Slice(hits, func(i, j int) bool {
		if hits[i].SourceDoc != hits[j].SourceDoc {
			return hits[i].SourceDoc < hits[j].SourceDoc
		}
		return hits[i].Source < hits[j].Source
	})
	return hits
}

// ---- Craft-hosted index store ----
//
// The index can live inside Craft itself (--links-store <pageBlockId>), so any
// session shares one warm index without a repo artifact. Layout owned by the
// tool inside the store page:
//   - a text block starting with "Обновлён:" — human-visible cutoff line;
//   - one or more code blocks — base64 of gzip of the compact LinksIndex JSON,
//     split into storeChunkSize pieces (Craft block size safety).
// Everything else in the doc (callout, headers) is left untouched. A dump that
// fails to decode is treated as absent: the next run rebuilds from scratch, so
// the store is self-healing.

const storeChunkSize = 60000

const cutoffPrefix = "Обновлён:"

// encodeIndex packs a LinksIndex into base64(gzip(compact JSON)) chunks.
func encodeIndex(idx LinksIndex) ([]string, error) {
	raw, err := json.Marshal(idx)
	if err != nil {
		return nil, err
	}
	var buf bytes.Buffer
	zw, _ := gzip.NewWriterLevel(&buf, gzip.BestCompression)
	if _, err := zw.Write(raw); err != nil {
		return nil, err
	}
	if err := zw.Close(); err != nil {
		return nil, err
	}
	b64 := base64.StdEncoding.EncodeToString(buf.Bytes())
	var chunks []string
	for len(b64) > 0 {
		n := storeChunkSize
		if n > len(b64) {
			n = len(b64)
		}
		chunks = append(chunks, b64[:n])
		b64 = b64[n:]
	}
	return chunks, nil
}

// decodeIndex reverses encodeIndex from concatenated chunk texts.
func decodeIndex(chunks []string) (LinksIndex, error) {
	var idx LinksIndex
	joined := strings.Join(chunks, "")
	bin, err := base64.StdEncoding.DecodeString(joined)
	if err != nil {
		return idx, fmt.Errorf("base64: %v", err)
	}
	zr, err := gzip.NewReader(bytes.NewReader(bin))
	if err != nil {
		return idx, fmt.Errorf("gzip: %v", err)
	}
	raw, err := io.ReadAll(zr)
	if err != nil {
		return idx, fmt.Errorf("gunzip: %v", err)
	}
	if err := json.Unmarshal(raw, &idx); err != nil {
		return idx, fmt.Errorf("json: %v", err)
	}
	return idx, nil
}

// cleanChunk strips code fences and whitespace a Craft round-trip may add.
func cleanChunk(md string) string {
	var out []string
	for _, line := range strings.Split(md, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "```") {
			continue
		}
		out = append(out, line)
	}
	return strings.Join(out, "")
}

// storeState is what loadStore learned about the store page: the decoded
// index plus the block IDs the tool owns (for the subsequent save).
type storeState struct {
	index    LinksIndex
	chunkIDs []string // existing code blocks, document order
	cutoffID string   // text block starting with cutoffPrefix ("" if absent)
	decodeOK bool
}

// loadStore reads the store page and decodes the dump. Decode failures are
// reported but non-fatal: the caller proceeds with an empty index.
func loadStore(client *Client, storeID string) (storeState, error) {
	st := storeState{index: LinksIndex{Docs: map[string]string{}}}
	root, err := client.getBlocksNoMeta(storeID)
	if err != nil {
		return st, err
	}
	var chunks []string
	var walk func(b Block)
	walk = func(b Block) {
		switch {
		case b.Type == "code":
			st.chunkIDs = append(st.chunkIDs, b.ID)
			text := b.RawCode
			if text == "" {
				text = b.Markdown
			}
			chunks = append(chunks, cleanChunk(text))
		case b.Type == "text" && st.cutoffID == "" && strings.HasPrefix(strings.TrimSpace(b.Markdown), cutoffPrefix):
			st.cutoffID = b.ID
		}
		for _, c := range b.Content {
			walk(c)
		}
	}
	walk(root)
	if len(chunks) == 0 {
		return st, nil
	}
	idx, err := decodeIndex(chunks)
	if err != nil {
		return st, fmt.Errorf("store dump undecodable (will rebuild): %v", err)
	}
	if idx.Docs == nil {
		idx.Docs = map[string]string{}
	}
	st.index = idx
	st.decodeOK = true
	return st, nil
}

// saveStore rewrites the tool-owned blocks: cutoff line updated (or created),
// old chunk blocks deleted, fresh chunks appended one by one (order-safe).
func saveStore(client *Client, storeID string, st storeState, idx LinksIndex) error {
	chunks, err := encodeIndex(idx)
	if err != nil {
		return err
	}
	cutoff := fmt.Sprintf("%s %s · доков: %d · ссылок: %d",
		cutoffPrefix, idx.GeneratedAt, len(idx.Docs), len(idx.Links))
	if st.cutoffID != "" {
		if err := client.updateBlocks([]map[string]any{{"id": st.cutoffID, "markdown": cutoff}}); err != nil {
			return fmt.Errorf("cutoff update: %v", err)
		}
	} else {
		if err := client.insertBlocks(storeID, []map[string]any{{"type": "text", "markdown": cutoff}}); err != nil {
			return fmt.Errorf("cutoff insert: %v", err)
		}
	}
	if len(st.chunkIDs) > 0 {
		if err := client.deleteBlocks(st.chunkIDs); err != nil {
			return fmt.Errorf("old chunks delete: %v", err)
		}
	}
	for i, ch := range chunks {
		block := map[string]any{"type": "code", "rawCode": ch, "language": "plaintext"}
		if err := client.insertBlocks(storeID, []map[string]any{block}); err != nil {
			return fmt.Errorf("chunk %d/%d insert: %v", i+1, len(chunks), err)
		}
	}
	return nil
}

// runBacklinks executes --backlinks/--links-refresh: load index (file or Craft
// store), refresh it against the live space (unless --offline), persist it
// back, answer the query.
func runBacklinks(client *Client, target, docsArg, excludeArg, linksPath, linksStore string, offline bool) {
	idx := LinksIndex{Docs: map[string]string{}}
	havePrior := false
	var store storeState

	res := BacklinksResult{Target: strings.ToLower(target)}

	switch {
	case linksStore != "":
		st, err := loadStore(client, linksStore)
		if err != nil && st.chunkIDs == nil && st.cutoffID == "" {
			fail("cannot read links store %s: %v", linksStore, err)
		}
		if err != nil {
			res.Errors = append(res.Errors, err.Error())
		}
		store = st
		idx = st.index
		havePrior = st.decodeOK
	case linksPath != "":
		if b, err := os.ReadFile(linksPath); err == nil {
			if err := json.Unmarshal(b, &idx); err != nil {
				fail("cannot parse links file %s: %v", linksPath, err)
			}
			if idx.Docs == nil {
				idx.Docs = map[string]string{}
			}
			havePrior = true
		}
	}

	if offline {
		if !havePrior {
			fail("--offline needs an existing --links-file or a readable --links-store dump")
		}
		res.Stale = true
	} else {
		listing, err := client.listDocuments()
		if err != nil {
			if !havePrior {
				fail("GET /documents failed and no prior index to fall back to: %v", err)
			}
			res.Stale = true
			res.Errors = append(res.Errors, fmt.Sprintf("listing failed, answering from stale index: %v", err))
		} else {
			exclude := toSet(excludeArg)
			if linksStore != "" {
				exclude[linksStore] = true // never index the store doc itself
			}
			fresh, scanned, skipped, errs := refreshLinksIndex(client, idx, listing, splitCSV(docsArg), exclude)
			idx = fresh
			res.ScannedDocs, res.SkippedDocs = scanned, skipped
			res.Errors = append(res.Errors, errs...)
			switch {
			case linksStore != "":
				if err := saveStore(client, linksStore, store, idx); err != nil {
					res.Errors = append(res.Errors, fmt.Sprintf("store save failed: %v", err))
				}
			case linksPath != "":
				out, _ := json.MarshalIndent(idx, "", " ")
				if err := os.WriteFile(linksPath, out, 0o644); err != nil {
					fail("cannot write links file %s: %v", linksPath, err)
				}
			}
		}
	}

	res.TotalLinks = len(idx.Links)
	res.IndexedAt = idx.GeneratedAt
	if target != "" {
		res.Backlinks = queryBacklinks(idx, target)
		res.Count = len(res.Backlinks)
	}
	out, _ := json.MarshalIndent(res, "", "  ")
	fmt.Println(string(out))
}

// ---- HTTP client ----

type Client struct {
	http      *http.Client
	base      string
	retries   int // transient (network / 5xx) retry budget
	rlRetries int // HTTP 429 'Block budget exceeded' retry budget (longer backoff)
}

func (c *Client) getBlocks(id string) (Block, error) {
	u := fmt.Sprintf("%s/blocks?id=%s&maxDepth=-1&fetchMetadata=true", c.base, url.QueryEscape(id))
	var b Block
	err := c.getJSON(u, &b)
	return b, err
}

// getBlocksNoMeta fetches a tree without metadata — enough for the links store.
func (c *Client) getBlocksNoMeta(id string) (Block, error) {
	u := fmt.Sprintf("%s/blocks?id=%s&maxDepth=-1", c.base, url.QueryEscape(id))
	var b Block
	err := c.getJSON(u, &b)
	return b, err
}

// insertBlocks POSTs new blocks to the end of parent page parentID.
func (c *Client) insertBlocks(parentID string, blocks []map[string]any) error {
	payload := map[string]any{
		"blocks":   blocks,
		"position": map[string]any{"position": "end", "pageId": parentID},
	}
	return c.sendJSON(http.MethodPost, c.base+"/blocks", payload)
}

// updateBlocks PUTs partial block updates ({id, markdown, ...}).
func (c *Client) updateBlocks(blocks []map[string]any) error {
	return c.sendJSON(http.MethodPut, c.base+"/blocks", map[string]any{"blocks": blocks})
}

// deleteBlocks removes blocks by ID.
func (c *Client) deleteBlocks(ids []string) error {
	return c.sendJSON(http.MethodDelete, c.base+"/blocks", map[string]any{"blockIds": ids})
}

// sendJSON issues a write request with the same transient/429 retry policy as
// reads. Writes are NOT retried on ambiguous transport errors after the body
// may have been consumed server-side — the store is self-healing (a broken
// dump just forces a rebuild), so at-most-once with an error surfaced beats
// duplicate chunks.
func (c *Client) sendJSON(method, u string, payload any) error {
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	rlLeft := c.rlRetries
	var backoff time.Duration
	for {
		if backoff > 0 {
			time.Sleep(backoff)
			backoff = 0
		}
		ctx, cancel := context.WithTimeout(context.Background(), c.http.Timeout)
		req, _ := http.NewRequestWithContext(ctx, method, u, bytes.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json")
		resp, err := c.http.Do(req)
		if err != nil {
			cancel()
			return err
		}
		respBody, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		cancel()
		if resp.StatusCode == http.StatusTooManyRequests && rlLeft > 0 {
			backoff = rateLimitBackoff(c.rlRetries - rlLeft)
			rlLeft--
			continue
		}
		if resp.StatusCode >= 400 {
			return fmt.Errorf("%s %s: HTTP %d: %s", method, u, resp.StatusCode, truncate(string(respBody), 300))
		}
		return nil
	}
}

func (c *Client) listDocuments() ([]Document, error) {
	u := fmt.Sprintf("%s/documents?fetchMetadata=true", c.base)
	var r docsResponse
	err := c.getJSON(u, &r)
	return r.Items, err
}

func (c *Client) getJSON(u string, out any) error {
	var lastErr error
	transientLeft := c.retries // network / 5xx
	rlLeft := c.rlRetries      // HTTP 429 'Block budget exceeded'
	var backoff time.Duration

	for {
		if backoff > 0 {
			time.Sleep(backoff)
			backoff = 0
		}
		ctx, cancel := context.WithTimeout(context.Background(), c.http.Timeout)
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
		req.Header.Set("Accept", "application/json")
		resp, err := c.http.Do(req)
		if err != nil {
			cancel()
			lastErr = err
			if transientLeft <= 0 {
				return lastErr
			}
			backoff = transientBackoff(c.retries - transientLeft)
			transientLeft--
			continue
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		cancel()

		switch {
		case resp.StatusCode == http.StatusNotFound:
			return fmt.Errorf("not found (HTTP 404)")
		case resp.StatusCode == http.StatusTooManyRequests:
			// Block budget exceeded: the window needs time to refill. Wait with a
			// longer backoff and retry rather than dropping the document.
			lastErr = fmt.Errorf("HTTP 429: %s", truncate(string(body), 200))
			if rlLeft <= 0 {
				return lastErr
			}
			backoff = rateLimitBackoff(c.rlRetries - rlLeft)
			rlLeft--
			continue
		case resp.StatusCode >= 500:
			lastErr = fmt.Errorf("HTTP %d: %s", resp.StatusCode, truncate(string(body), 200))
			if transientLeft <= 0 {
				return lastErr
			}
			backoff = transientBackoff(c.retries - transientLeft)
			transientLeft--
			continue
		case resp.StatusCode >= 400:
			return fmt.Errorf("HTTP %d: %s", resp.StatusCode, truncate(string(body), 200)) // other 4xx: don't hammer
		}
		if err := json.Unmarshal(body, out); err != nil {
			return fmt.Errorf("bad JSON: %v", err)
		}
		return nil
	}
}

// transientBackoff: 2s, 4s, 8s, … for network/5xx retries (n is 0-based).
func transientBackoff(n int) time.Duration {
	return time.Duration(1<<uint(n+1)) * time.Second
}

// rateLimitBackoff: 5s, 10s, 20s, 40s, 60s (capped) for HTTP 429, giving the
// block-budget window time to refill (n is 0-based).
func rateLimitBackoff(n int) time.Duration {
	d := time.Duration(5<<uint(n)) * time.Second
	if d > 60*time.Second {
		d = 60 * time.Second
	}
	return d
}

// ---- helpers ----

func blockTime(b Block) time.Time {
	if b.Metadata == nil || b.Metadata.LastModifiedAt == "" {
		return time.Time{}
	}
	t, err := parseTime(b.Metadata.LastModifiedAt)
	if err != nil {
		return time.Time{}
	}
	return t
}

func parseTime(s string) (time.Time, error) {
	for _, layout := range []string{time.RFC3339Nano, time.RFC3339} {
		if t, err := time.Parse(layout, s); err == nil {
			return t, nil
		}
	}
	return time.Time{}, fmt.Errorf("unrecognized time %q", s)
}

func maxT(a, b time.Time) time.Time {
	if b.After(a) {
		return b
	}
	return a
}

func firstLine(s string) string {
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		s = s[:i]
	}
	return strings.TrimSpace(s)
}

func splitCSV(s string) []string {
	var out []string
	for _, p := range strings.Split(s, ",") {
		if p = strings.TrimSpace(p); p != "" {
			out = append(out, p)
		}
	}
	return out
}

func toSet(s string) map[string]bool {
	m := map[string]bool{}
	for _, p := range splitCSV(s) {
		m[p] = true
	}
	return m
}

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n] + "…"
	}
	return s
}

func fail(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "craft-sync: "+format+"\n", args...)
	os.Exit(1)
}
