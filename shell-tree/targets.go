package main

import (
	"strings"
)

// Flags of the world-changing commands that take a value of their own. Without
// them `truncate -s 0 README.md` would report «0» as a file it writes — a name
// nobody wrote.
var mutatingValueFlags = map[string]map[string]bool{
	"truncate": set("-s", "--size", "-r", "--reference"),
	"install":  set("-m", "--mode", "-o", "--owner", "-g", "--group", "-t", "--target-directory", "-S", "--suffix"),
	"ln":       set("-t", "--target-directory", "-S", "--suffix"),
	"cp":       set("-t", "--target-directory", "-S", "--suffix"),
	"mv":       set("-t", "--target-directory", "-S", "--suffix"),
	"chmod":    set("--reference"),
	"chown":    set("--reference"),
	"chgrp":    set("--reference"),
	"mkdir":    set("-m", "--mode"),
	"mkfifo":   set("-m", "--mode"),
	"mknod":    set("-m", "--mode"),
	"shred":    set("-n", "--iterations", "-s", "--size"),
	"kill":     set("-s", "--signal", "-n"),
	"pkill":    set("-s", "--signal", "-u", "--euid", "-P", "--parent"),
	"killall":  set("-s", "--signal", "-u", "--user"),
	"tar":      set("-f", "--file", "-C", "--directory"),
	"zip":      set("-t", "-n"),
	"curl":     set("-o", "--output", "-H", "--header", "-d", "--data", "--data-raw", "-X", "--request", "-u", "--user", "-A", "--user-agent", "-e", "--referer", "-T", "--upload-file", "--url", "-F", "--form", "-b", "--cookie", "-c", "--cookie-jar", "--connect-timeout", "-m", "--max-time"),
	"wget":     set("-O", "--output-document", "-P", "--directory-prefix", "--header", "-o", "--output-file", "-U", "--user-agent", "-T", "--timeout"),
	"dd":       set(),
	"tee":      set(),
	"rm":       set(),
	"rmdir":    set(),
	"touch":    set("-d", "--date", "-r", "--reference", "-t"),
}

// Commands whose first operand is not a path: `chmod +x file`, `chown user file`.
var skipFirstOperand = set("chmod", "chown", "chgrp")

// targetsOf names the places a world-changing command writes to. What cannot be
// named is left out: the verdict still says «да», and the reason says why.
func (j *judge) targetsOf(name string, args []Word, cwd string) []Target {
	operands := operandsOf(name, args)
	switch name {
	case "mkdir", "rmdir":
		return pathTargets(operands, kindDir, name, cwd)
	case "rm":
		kind := kindFile
		if recursive(args) {
			kind = kindDir
		}
		return pathTargets(operands, kind, name, cwd)
	case "cp", "install", "ln":
		if len(operands) < 2 {
			return pathTargets(operands, kindFile, name, cwd)
		}
		return pathTargets(operands[len(operands)-1:], kindFile, name, cwd)
	case "dd":
		return ddTargets(args, cwd)
	case "kill", "killall", "pkill":
		return pathTargets(operands, kindProcess, name, cwd)
	case "curl", "wget":
		return networkTargets(name, args, operands, cwd)
	case "tar", "zip", "unzip", "gunzip":
		// Что распакуется или запакуется, знает сам архив: цель не называем.
		return nil
	default:
		return pathTargets(operands, kindFile, name, cwd)
	}
}

// operandsOf keeps the arguments that are paths: no flags, no flag values, and
// no leading mode or owner.
func operandsOf(name string, args []Word) []Word {
	valued := mutatingValueFlags[name]
	var out []Word
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if arg.Text == "--" {
			out = append(out, args[i+1:]...)
			break
		}
		if strings.HasPrefix(arg.Text, "-") && arg.Text != "-" {
			if valued[arg.Text] {
				i++
			}
			continue
		}
		out = append(out, arg)
	}
	if skipFirstOperand[name] && len(out) > 0 {
		out = out[1:]
	}
	return out
}

func recursive(args []Word) bool {
	for _, arg := range args {
		if arg.Text == "--recursive" || arg.Text == "--dir" {
			return true
		}
		if !strings.HasPrefix(arg.Text, "-") || strings.HasPrefix(arg.Text, "--") {
			continue
		}
		if strings.ContainsAny(arg.Text[1:], "rRd") {
			return true
		}
	}
	return false
}

func pathTargets(operands []Word, kind, via, cwd string) []Target {
	var out []Target
	for _, operand := range operands {
		if !operand.Literal {
			continue
		}
		path := operand.Text
		if kind != kindProcess {
			path = resolve(cwd, path)
		}
		out = append(out, Target{Path: path, Kind: kind, Via: via})
	}
	return out
}

// ddTargets reads `of=…`: dd names its target by an operand, not by a flag.
func ddTargets(args []Word, cwd string) []Target {
	var out []Target
	for _, arg := range args {
		if !arg.Literal || !strings.HasPrefix(arg.Text, "of=") {
			continue
		}
		out = append(out, Target{Path: resolve(cwd, strings.TrimPrefix(arg.Text, "of=")), Kind: kindFile, Via: "dd"})
	}
	return out
}

// networkTargets splits what goes out (the address) from what lands on disk
// (the file the answer is saved into).
func networkTargets(name string, args, operands []Word, cwd string) []Target {
	var out []Target
	for _, operand := range operands {
		if operand.Literal {
			out = append(out, Target{Path: operand.Text, Kind: kindNetwork, Via: name})
		}
	}
	if len(out) == 0 {
		out = append(out, Target{Kind: kindNetwork, Via: name})
	}
	saved := set("-o", "--output", "-O", "--output-document")
	for i := 0; i < len(args)-1; i++ {
		if saved[args[i].Text] && args[i+1].Literal {
			out = append(out, Target{Path: resolve(cwd, args[i+1].Text), Kind: kindFile, Via: name})
		}
	}
	return out
}

// subTargets names what a world-changing subcommand writes to. A git command
// that talks to a remote gets a target of kind «сеть» even when the remote is
// not written out — an outgoing command has an addressee in any case. The
// repository itself is named only when the directory is known.
func (j *judge) subTargets(name, sub string, args, rest []Word, cwd string) []Target {
	if name != "git" {
		return nil
	}
	if contains([]string{"push", "fetch", "pull", "clone"}, sub) {
		for _, arg := range rest {
			if strings.HasPrefix(arg.Text, "-") || !arg.Literal {
				continue
			}
			return []Target{{Path: arg.Text, Kind: kindNetwork, Via: "git " + sub}}
		}
		return []Target{{Kind: kindNetwork, Via: "git " + sub}}
	}
	repo := repoDir(args, cwd)
	if repo == "" {
		return nil
	}
	return []Target{{Path: repo, Kind: kindGit, Via: "git " + sub}}
}

// repoDir is the repository the git command works on: `-C` when it is there,
// the directory of the link otherwise.
func repoDir(args []Word, cwd string) string {
	for i := 0; i < len(args)-1; i++ {
		if args[i].Text == "-C" && args[i+1].Literal {
			return resolve(cwd, args[i+1].Text)
		}
	}
	return cwd
}
