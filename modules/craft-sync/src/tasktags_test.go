package main

import (
	"reflect"
	"testing"
)

func task(id, md, state string, content ...Block) Block {
	return Block{ID: id, Type: "page", Markdown: md, ListStyle: "task", TaskInfo: &TaskInfo{State: state}, Content: content}
}

// mismatchIDs оставляет от ответа только id: сам набор и его порядок — это
// и есть поведение, которое видит вызывающий.
func mismatchIDs(ms []TaskMismatch) []string {
	ids := []string{}
	for _, m := range ms {
		ids = append(ids, m.ID)
	}
	return ids
}

func TestTaskTagsFindsCheckboxTagMismatch(t *testing.T) {
	root := Block{ID: "doc", Type: "page", Markdown: "Проект", Content: []Block{
		task("ok-open", "- [ ] Повесить карнизы #задача/1ч", "todo"),
		task("ok-done", "- [x] Залить стяжку #задача/готово", "done"),
		task("ok-cancel", "- [ ] ~Купить ванну #задача/отменено~", "canceled"),
		task("ticked", "- [x] Уложить плитку #задача", "done"),
		task("tag-only", "- [ ] Выбрать бригаду #задача/готово", "todo"),
		task("cancel-no-tag", "- [ ] Купить трап #задача", "canceled"),
	}}
	got := mismatchIDs(findTaskTagMismatches(root))
	want := []string{"ticked", "tag-only", "cancel-no-tag"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("несоответствия = %v, ждали %v", got, want)
	}
}

func TestTaskTagsIgnoresChecklistItemsAndLinkedTags(t *testing.T) {
	root := Block{ID: "doc", Type: "page", Markdown: "Проект", Content: []Block{
		// пункт чек-листа без тега — не задача базы
		task("item", "- [x] Стяжка залита", "done"),
		// тег живёт в тексте ссылки на другую задачу, а не у самой задачи
		task("linked", "- [ ] Уложить плитку #задача, после [Залить стяжку #задача/готово](block://x)", "todo"),
	}}
	if got := findTaskTagMismatches(root); len(got) != 0 {
		t.Fatalf("ждали пусто, получили %v", mismatchIDs(got))
	}
}

func TestTaskTagsWalksNestedPagesAndCollectionsWithPath(t *testing.T) {
	stage := task("stage", "- [ ] ### Ванная отремонтирована #этап", "todo",
		task("deep", "- [x] Установить свет #задача", "done"))
	coll := Block{ID: "coll", Type: "collection", Items: []Block{
		task("item-task", "- [x] Купить плитку #задача", "done"),
	}}
	root := Block{ID: "doc", Type: "page", Markdown: "Проект", Content: []Block{stage, coll}}
	got := findTaskTagMismatches(root)
	if ids := mismatchIDs(got); !reflect.DeepEqual(ids, []string{"deep", "item-task"}) {
		t.Fatalf("несоответствия = %v", ids)
	}
	want := TaskMismatch{ID: "deep", Title: "Установить свет #задача", State: "done", Tag: "#задача",
		Path: []string{"Проект", "Ванная отремонтирована #этап"}}
	if !reflect.DeepEqual(got[0], want) {
		t.Fatalf("запись = %+v, ждали %+v", got[0], want)
	}
}

func TestTaskTagsChecksStagesAndMilestones(t *testing.T) {
	root := Block{ID: "doc", Type: "page", Markdown: "Проект", Content: []Block{
		task("stage-ok", "- [x] ### Стяжка залита #этап/готово", "done"),
		task("stage-ticked", "- [x] ### Плитка уложена #этап", "done"),
		task("milestone-ok", "- [ ] ## Ванная готова #веха", "todo"),
		task("milestone-tag-only", "- [ ] ## Кухня готова #веха/готово", "todo"),
		task("milestone-cancel", "- [ ] ## Балкон утеплён #веха", "canceled"),
	}}
	got, scanned := scanTaskTags(root)
	want := []string{"stage-ticked", "milestone-tag-only", "milestone-cancel"}
	if ids := mismatchIDs(got); !reflect.DeepEqual(ids, want) {
		t.Fatalf("несоответствия = %v, ждали %v", ids, want)
	}
	if scanned != 5 {
		t.Fatalf("просмотрено %d, ждали 5", scanned)
	}
}

func TestTaskTagsCountsOnlyTheTypeTagAtTheEndOfTitle(t *testing.T) {
	// тег типа стоит в самом конце названия; тот же тег посреди текста — часть названия
	root := Block{ID: "doc", Type: "page", Markdown: "Проект", Content: []Block{
		task("quoted", "- [x] Завести шаблоны «10. Этап #этап» и «11. Веха #веха» #задача/готово", "done"),
		// пункт чек-листа, где тег только в названии шаблона в кавычках
		task("item-quoted", "- [x] Шаблон «10. Этап #этап» заведён", "done"),
	}}
	if got := findTaskTagMismatches(root); len(got) != 0 {
		t.Fatalf("ждали пусто, получили %v", mismatchIDs(got))
	}
}
