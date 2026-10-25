package main

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"
)

// Режим --task-tags: чекбокс задачи и её тег говорят об одном и том же — сделана
// она или нет. Отметку Влад ставит руками в приложении, тег при этом не меняется,
// поэтому расхождение и есть сигнал «отмечено, но не отражено». Инструмент только
// находит такие задачи; что с ними делать, решает агент с Владом.

// TaskInfo — состояние задачи, как его отдаёт GET /blocks.
type TaskInfo struct {
	State string `json:"state"`
}

// TaskMismatch — задача, у которой состояние чекбокса и тег расходятся.
type TaskMismatch struct {
	ID    string   `json:"id"`
	Title string   `json:"title"`
	State string   `json:"state"`
	Tag   string   `json:"tag"`
	Path  []string `json:"path"`
}

type TaskTagsResult struct {
	Mismatches   []TaskMismatch `json:"mismatches"`
	ScannedTasks int            `json:"scannedTasks"`
	Errors       []string       `json:"errors"`
}

// Тег у ссылки на другую задачу принадлежит ей, а не текущей: ссылки вырезаются
// до поиска тега.
var (
	mdLinkRe   = regexp.MustCompile(`\[[^\]]*\]\([^)]*\)`)
	taskTagRe  = regexp.MustCompile(`#задача(/[^\s#~*,]+)?`)
	listMarkRe = regexp.MustCompile(`^\s*(- \[[ x]\] |- |\+ )?(#+ )?`)
)

// closingTagByState — какой тег обязан стоять при каждом состоянии чекбокса.
var closingTagByState = map[string]string{"done": "/готово", "canceled": "/отменено"}

func findTaskTagMismatches(root Block) []TaskMismatch {
	out, _ := scanTaskTags(root)
	return out
}

// scanTaskTags возвращает и найденное, и число просмотренных задач: сверка
// «просмотрено против найдено» печатается рядом с ответом, чтобы пустой список
// не выглядел как пустой проект.
func scanTaskTags(root Block) ([]TaskMismatch, int) {
	out, scanned := []TaskMismatch{}, 0
	walkTaskTags(root, nil, &out, &scanned)
	return out, scanned
}

func walkTaskTags(b Block, path []string, out *[]TaskMismatch, scanned *int) {
	title := blockTitle(b.Markdown)
	m, isTask, mismatched := taskTagMismatch(b, title, path)
	if isTask {
		*scanned++
	}
	if mismatched {
		*out = append(*out, m)
	}
	childPath := path
	if b.Type == "page" && title != "" {
		childPath = append(append([]string{}, path...), title)
	}
	for _, c := range b.Content {
		walkTaskTags(c, childPath, out, scanned)
	}
	for _, c := range b.Items {
		walkTaskTags(c, childPath, out, scanned)
	}
}

// taskTagMismatch отвечает, задача ли это базы и расходятся ли у неё чекбокс и тег.
func taskTagMismatch(b Block, title string, path []string) (m TaskMismatch, isTask, mismatched bool) {
	if b.ListStyle != "task" || b.TaskInfo == nil {
		return m, false, false
	}
	tag := taskTagRe.FindString(mdLinkRe.ReplaceAllString(b.Markdown, ""))
	if tag == "" {
		return m, false, false // пункт чек-листа, не задача базы
	}
	suffix := strings.TrimPrefix(tag, "#задача")
	want, closed := closingTagByState[b.TaskInfo.State]
	isClosingTag := suffix == "/готово" || suffix == "/отменено"
	if (closed && suffix == want) || (!closed && !isClosingTag) {
		return m, true, false
	}
	return TaskMismatch{ID: b.ID, Title: title, State: b.TaskInfo.State, Tag: tag, Path: path}, true, true
}

func blockTitle(md string) string {
	line := listMarkRe.ReplaceAllString(firstLine(md), "")
	return strings.Trim(strings.TrimSpace(line), "~")
}

func runTaskTags(client *Client, idsArg string) {
	res := TaskTagsResult{Mismatches: []TaskMismatch{}, Errors: []string{}}
	ids := splitCSV(idsArg)
	for _, id := range ids {
		root, err := client.getBlocksNoMeta(id)
		if err != nil {
			res.Errors = append(res.Errors, fmt.Sprintf("%s: %v", id, err))
			continue
		}
		found, scanned := scanTaskTags(root)
		res.Mismatches = append(res.Mismatches, found...)
		res.ScannedTasks += scanned
	}
	enc := json.NewEncoder(os.Stdout)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(res); err != nil {
		fail("cannot print result: %v", err)
	}
	if len(res.Errors) == len(ids) {
		os.Exit(1) // ничего не прочиталось: пустой ответ не должен выглядеть как «расхождений нет»
	}
}
