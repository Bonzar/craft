package main

import (
	"path"
	"strings"

	"mvdan.cc/sh/v3/syntax"
)

// Parse builds the tree of a command line. Cwd is the directory the line is
// launched from; an empty one means it is not known, and then no relative path
// is resolved.
func Parse(src, cwd string) (*Tree, error) {
	file, err := syntax.NewParser(syntax.Variant(syntax.LangBash)).Parse(strings.NewReader(src), "")
	if err != nil {
		return nil, err
	}
	return treeOf(file.Stmts, cwd), nil
}

func treeOf(stmts []*syntax.Stmt, cwd string) *Tree {
	b := &builder{dirs: dirs{cwd: cwd}}
	b.stmts(stmts, "", "")
	return &Tree{Cwd: cwd, Links: b.links}
}

// dirs is the working directory as the line walks on. Stack serves pushd/popd.
type dirs struct {
	cwd   string
	stack []string
}

type builder struct {
	links []Link
	dirs  dirs
}

func (b *builder) stmts(list []*syntax.Stmt, op, inside string) {
	for i, st := range list {
		next := op
		if i > 0 {
			next = ";"
		}
		b.stmt(st, next, inside)
	}
}

func (b *builder) stmt(st *syntax.Stmt, op, inside string) {
	if bin, ok := st.Cmd.(*syntax.BinaryCmd); ok {
		switch bin.Op {
		case syntax.AndStmt, syntax.OrStmt:
			joint := "&&"
			if bin.Op == syntax.OrStmt {
				joint = "||"
			}
			b.stmt(bin.X, op, inside)
			b.stmt(bin.Y, joint, inside)
			return
		case syntax.Pipe, syntax.PipeAll:
			b.pipeline(st, op, inside)
			return
		}
	}
	if b.compound(st, op, inside) {
		return
	}
	b.simple(st, op, inside)
}

// compound walks the constructs that hold statements of their own and says
// whether it took the statement.
func (b *builder) compound(st *syntax.Stmt, op, inside string) bool {
	switch cmd := st.Cmd.(type) {
	case *syntax.Subshell:
		// Подоболочка получает свою копию каталогов: её `cd` наружу не выходит.
		outer := b.dirs
		b.stmts(cmd.Stmts, op, insideSubshell)
		b.dirs = outer
	case *syntax.Block:
		b.stmts(cmd.Stmts, op, insideBlock)
	case *syntax.IfClause:
		b.clause(cmd, op, insideIf)
	case *syntax.ForClause:
		b.stmts(cmd.Do, op, insideFor)
	case *syntax.WhileClause:
		b.stmts(cmd.Cond, op, insideWhile)
		b.stmts(cmd.Do, op, insideWhile)
	case *syntax.CaseClause:
		for _, item := range cmd.Items {
			b.stmts(item.Stmts, op, insideCase)
		}
	case *syntax.FuncDecl:
		b.stmt(cmd.Body, op, insideFunction)
	case *syntax.TimeClause:
		// `time команда` — ключевое слово оболочки, а не команда: судить надо
		// то, что оно замерило.
		if cmd.Stmt == nil {
			return false
		}
		b.stmt(cmd.Stmt, op, inside)
	default:
		return false
	}
	return true
}

func (b *builder) clause(cmd *syntax.IfClause, op, inside string) {
	b.stmts(cmd.Cond, op, inside)
	b.stmts(cmd.Then, op, inside)
	if cmd.Else != nil {
		b.clause(cmd.Else, op, inside)
	}
}

// pipeline lays a pipeline out. When every stage is a simple command, the whole
// pipeline is one link; a compound stage (a subshell, a loop) gets links of its
// own. Either way each stage runs in a subshell of its own, so its `cd` stays
// inside it.
func (b *builder) pipeline(st *syntax.Stmt, op, inside string) {
	stages := stages(st, nil)
	simple := true
	for _, stage := range stages {
		if _, ok := stage.Cmd.(*syntax.CallExpr); !ok {
			simple = false
		}
	}
	if !simple {
		for i, stage := range stages {
			joint := op
			if i > 0 {
				joint = "|"
			}
			outer := b.dirs
			b.stmt(stage, joint, insidePipeline)
			b.dirs = outer
		}
		return
	}
	outer := b.dirs
	link := Link{Index: len(b.links), Op: op, Background: st.Background, Cwd: b.dirs.cwd, Inside: inside}
	for _, stage := range stages {
		link.Commands = append(link.Commands, b.command(stage, stage.Cmd.(*syntax.CallExpr)))
	}
	b.links = append(b.links, link)
	b.dirs = outer
}

func stages(st *syntax.Stmt, out []*syntax.Stmt) []*syntax.Stmt {
	bin, ok := st.Cmd.(*syntax.BinaryCmd)
	if ok && (bin.Op == syntax.Pipe || bin.Op == syntax.PipeAll) && len(st.Redirs) == 0 {
		return stages(bin.Y, stages(bin.X, out))
	}
	return append(out, st)
}

// simple emits a link for one command. Anything the walker does not take apart
// (`let`, `declare`, `[[ … ]]`, an arithmetic command) becomes a single word of
// its own source text, not literal — so the verdict answers «неизвестно» about
// it instead of reading it as a known command.
func (b *builder) simple(st *syntax.Stmt, op, inside string) {
	if st.Cmd == nil && len(st.Redirs) == 0 {
		return
	}
	var command Command
	switch cmd := st.Cmd.(type) {
	case *syntax.CallExpr:
		command = b.command(st, cmd)
	case *syntax.DeclClause:
		// `export FOO=1`, `declare -r X` — судить их надо по имени, как команду.
		command = Command{Words: append([]Word{{Text: cmd.Variant.Value, Literal: true}}, assignWords(cmd.Args)...)}
		command.Redirects = b.redirects(st.Redirs)
	case nil:
		// Голое перенаправление: команды нет, а файл обрезается.
		command = Command{Redirects: b.redirects(st.Redirs)}
	default:
		command = Command{Construct: construct(cmd), Words: []Word{{Text: source(cmd), Literal: false}}}
		command.Redirects = b.redirects(st.Redirs)
	}
	b.links = append(b.links, Link{
		Index:      len(b.links),
		Op:         op,
		Background: st.Background,
		Cwd:        b.dirs.cwd,
		Inside:     inside,
		Commands:   []Command{command},
	})
	b.walkDir(command)
}

// construct names the shell construct the walker does not take apart, so the
// verdict can say what it does not know instead of guessing a command name.
func construct(cmd syntax.Command) string {
	switch cmd.(type) {
	case *syntax.TestClause:
		return "проверка"
	case *syntax.LetClause:
		return "let"
	case *syntax.ArithmCmd:
		return "арифметика"
	case *syntax.CoprocClause:
		return "coproc"
	default:
		return "конструкция оболочки"
	}
}

func assignWords(list []*syntax.Assign) []Word {
	var out []Word
	for _, assign := range list {
		out = append(out, Word{Text: source(assign), Literal: assign.Value == nil || wordOf(assign.Value).Literal})
	}
	return out
}

func (b *builder) command(st *syntax.Stmt, call *syntax.CallExpr) Command {
	command := Command{Redirects: b.redirects(st.Redirs)}
	for _, assign := range call.Assigns {
		command.Assignments = append(command.Assignments, Assignment{Name: assign.Name.Value, Value: wordOf(assign.Value)})
	}
	command.Words, command.Wrappers = peel(words(call.Args))
	command.Shell = b.shell(command.Words)
	command.Substitutions = b.substitutions(call, st.Redirs)
	return command
}

func (b *builder) redirects(list []*syntax.Redirect) []Redirect {
	var out []Redirect
	for _, r := range list {
		redirect := Redirect{Op: r.Op.String(), Mode: mode(r.Op), Target: wordOf(r.Word)}
		if r.N != nil {
			redirect.Fd = r.N.Value
		}
		if r.Hdoc != nil {
			redirect.Body = wordOf(r.Hdoc).Text
		}
		out = append(out, redirect)
	}
	return out
}

func mode(op syntax.RedirOperator) string {
	switch op {
	case syntax.RdrOut, syntax.ClbOut, syntax.RdrAll, syntax.RdrInOut:
		return modeOverwrite
	case syntax.AppOut, syntax.AppAll:
		return modeAppend
	case syntax.RdrIn:
		return modeRead
	case syntax.Hdoc, syntax.DashHdoc:
		return modeHeredoc
	case syntax.WordHdoc:
		return modeString
	default:
		return modeCopy
	}
}

// substitutions collects `$(…)`, backticks and process substitutions from the
// words and the redirection targets, each as a tree of its own.
func (b *builder) substitutions(call *syntax.CallExpr, redirs []*syntax.Redirect) []Tree {
	var out []Tree
	collect := func(node syntax.Node) {
		syntax.Walk(node, func(n syntax.Node) bool {
			switch sub := n.(type) {
			case *syntax.CmdSubst:
				out = append(out, *treeOf(sub.Stmts, b.dirs.cwd))
			case *syntax.ProcSubst:
				out = append(out, *treeOf(sub.Stmts, b.dirs.cwd))
			}
			return true
		})
	}
	for _, assign := range call.Assigns {
		if assign.Value != nil {
			collect(assign.Value)
		}
	}
	for _, w := range call.Args {
		collect(w)
	}
	for _, r := range redirs {
		if r.Word != nil {
			collect(r.Word)
		}
	}
	return out
}

// walkDir applies the directory moves of a link to the links after it. Only a
// lone `cd`, `pushd` or `popd` moves anything: inside a pipeline or a subshell
// the move dies with the stage, and the builder has isolated the state there.
func (b *builder) walkDir(command Command) {
	if len(command.Words) == 0 {
		return
	}
	switch command.Words[0].Text {
	case "cd":
		target, ok := operand(command.Words[1:])
		b.dirs.cwd = moved(b.dirs.cwd, target, ok)
	case "pushd":
		target, ok := operand(command.Words[1:])
		b.dirs.stack = append(b.dirs.stack, b.dirs.cwd)
		b.dirs.cwd = moved(b.dirs.cwd, target, ok)
	case "popd":
		if len(b.dirs.stack) == 0 {
			b.dirs.cwd = ""
			return
		}
		b.dirs.cwd = b.dirs.stack[len(b.dirs.stack)-1]
		b.dirs.stack = b.dirs.stack[:len(b.dirs.stack)-1]
	}
}

// operand is the first argument that is not a flag.
func operand(list []Word) (Word, bool) {
	for _, w := range list {
		if strings.HasPrefix(w.Text, "-") {
			continue
		}
		return w, true
	}
	return Word{}, false
}

// moved is the directory after a `cd`. Anything we cannot read literally —
// `cd` with no argument, `cd -`, `cd $DIR` — makes the directory unknown, and
// unknown it stays until the next `cd` we can read.
func moved(cwd string, target Word, ok bool) string {
	if !ok || !target.Literal {
		return ""
	}
	return resolve(cwd, target.Text)
}

// resolve reads a path the way the shell would from cwd. An unknown cwd leaves
// a relative path as it stands: guessing a root would name a file nobody wrote.
func resolve(cwd, target string) string {
	if strings.HasPrefix(target, "/") {
		return path.Clean(target)
	}
	if cwd == "" {
		return target
	}
	return path.Join(cwd, target)
}
