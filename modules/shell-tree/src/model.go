package main

// The shape of the tree: what `shell-tree parse` prints and what the verdict
// judges. The names of the modes, the kinds and the answers are the words of
// the spec, and they go out in JSON as they are.

// Redirect modes, as the spec names them.
const (
	modeOverwrite = "перезапись"
	modeAppend    = "дозапись"
	modeRead      = "чтение"
	modeHeredoc   = "heredoc"
	modeString    = "строка"
	modeCopy      = "копия"
)

// Names of the constructs a link can sit inside. A link inside a subshell or a
// pipeline stage cannot change the working directory of its neighbours, and the
// builder keeps that apart by isolating the directory state, not by these names
// — they are here for the reader of the tree.
const (
	insideSubshell = "подоболочка"
	insidePipeline = "конвейер"
	insideBlock    = "группа"
	insideIf       = "если"
	insideFor      = "цикл for"
	insideWhile    = "цикл while"
	insideCase     = "выбор case"
	insideFunction = "функция"
)

// Tree is a whole command line: a flat sequence of links in execution order.
// Nesting hangs off the commands — a command substitution and a `bash -c`
// string each carry a tree of their own.
type Tree struct {
	Cwd   string `json:"cwd"`
	Links []Link `json:"links"`
}

// Link is one pipeline together with the operator that joined it to the
// previous link. Cwd is the directory this link runs in, counted from the
// entry directory through the `cd` and `pushd` of the links before it; an empty
// Cwd means the directory is not known.
type Link struct {
	Index      int       `json:"index"`
	Op         string    `json:"op"`
	Background bool      `json:"background"`
	Cwd        string    `json:"cwd"`
	Inside     string    `json:"inside,omitempty"`
	Commands   []Command `json:"commands"`
}

// Command is one simple command: the words left after the launch wrappers were
// peeled off, plus everything that hangs on it. Construct is filled for what
// the walker does not take apart — `[[ … ]]`, `let`, arithmetic — and then the
// only word is the source text of that construct.
type Command struct {
	Words         []Word       `json:"words"`
	Construct     string       `json:"construct,omitempty"`
	Assignments   []Assignment `json:"assignments,omitempty"`
	Wrappers      []Wrapper    `json:"wrappers,omitempty"`
	Redirects     []Redirect   `json:"redirects,omitempty"`
	Substitutions []Tree       `json:"substitutions,omitempty"`
	Shell         *Shell       `json:"shell,omitempty"`
}

type Assignment struct {
	Name  string `json:"name"`
	Value Word   `json:"value"`
}

// Wrapper is a launch wrapper taken off the front of a command: `sudo`, `env`,
// `nice`, `time`, `timeout`, `xargs` and their like. Args are the wrapper's own
// arguments, not the wrapped command's.
type Wrapper struct {
	Name string `json:"name"`
	Args []Word `json:"args,omitempty"`
}

// Redirect is one redirection. Body carries the here-document text.
type Redirect struct {
	Op     string `json:"op"`
	Mode   string `json:"mode"`
	Fd     string `json:"fd,omitempty"`
	Target Word   `json:"target"`
	Body   string `json:"body,omitempty"`
}

// Shell is a shell wrapper that carries its script as a string: `bash -c '…'`.
// Tree is that string parsed; it is nil when the string did not parse, and then
// Error says why.
type Shell struct {
	Name   string `json:"name"`
	Script Word   `json:"script"`
	Tree   *Tree  `json:"tree,omitempty"`
	Error  string `json:"error,omitempty"`
}
