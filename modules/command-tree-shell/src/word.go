package main

import (
	"strings"

	"mvdan.cc/sh/v3/syntax"
)

// Word is one shell word after quote removal.
//
// Literal says whether the whole word is known from the source text alone.
// A word carrying a parameter, a command substitution, a glob, a brace list or
// a leading tilde is NOT literal: its real value is decided by the shell at run
// time, and naming it as a path would be a guess. Text still holds the best
// reading we have (substitutions are kept as written), so a caller can show the
// word to a human.
type Word struct {
	Text    string `json:"text"`
	Literal bool   `json:"literal"`
}

// expansion characters that make a literal part stop being one.
const globChars = "*?["
const braceChars = "{}"

func wordOf(w *syntax.Word) Word {
	if w == nil {
		return Word{Literal: false}
	}
	var text strings.Builder
	literal := true
	for _, part := range w.Parts {
		t, ok := partOf(part)
		text.WriteString(t)
		literal = literal && ok
	}
	value := text.String()
	if strings.HasPrefix(value, "~") {
		literal = false // домашний каталог знает только оболочка
	}
	return Word{Text: value, Literal: literal}
}

// partOf renders one word part and says whether it is literally known.
func partOf(part syntax.WordPart) (string, bool) {
	switch p := part.(type) {
	case *syntax.Lit:
		return p.Value, !strings.ContainsAny(p.Value, globChars) && !strings.ContainsAny(p.Value, braceChars)
	case *syntax.SglQuoted:
		return p.Value, true
	case *syntax.DblQuoted:
		var text strings.Builder
		literal := true
		for _, inner := range p.Parts {
			t, ok := partOf(inner)
			text.WriteString(t)
			literal = literal && ok
		}
		return text.String(), literal
	default:
		return source(part), false
	}
}

// source prints a node back as shell text. Used for the parts we cannot resolve
// (parameters, substitutions, arithmetic): the caller still sees what stood
// there, marked as not literal.
func source(node syntax.Node) string {
	var out strings.Builder
	if err := syntax.NewPrinter().Print(&out, node); err != nil {
		return ""
	}
	return strings.TrimRight(out.String(), "\n")
}

func words(list []*syntax.Word) []Word {
	if len(list) == 0 {
		return nil
	}
	out := make([]Word, 0, len(list))
	for _, w := range list {
		out = append(out, wordOf(w))
	}
	return out
}
