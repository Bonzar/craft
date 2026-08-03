package main

// Счёт покрытия: у скольких площадок есть РАБОТАЮЩИЙ инструмент.
//
// Смысл файла — отобрать у агента право самому решать, что работа закончена.
// Прошлые версии этого счётчика считали покрытой площадку с проставленным видом
// канала, то есть верили пометке, которую агент сам и ставит. Здесь покрытие
// выводится из двух вещей, ни одну из которых назначить себе нельзя:
//
//   - LastOk — время, когда канал площадки ответил живьём непустой афишей. Его
//     пишет только опрос (probe_run.go), и только при доказанной живости.
//   - улика источника — слова самой сети о том, что площадка не работает.
//
// Всё остальное считается недоработкой, даже если выглядит объяснённым.

import (
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
)

// CoverageGap — площадка, у которой рабочего инструмента нет.
type CoverageGap struct {
	Key    string `json:"key"`
	Name   string `json:"name"`
	Reason string `json:"reason"`
	// Kind непустой означает, что канал назначен, но живого ответа от него так
	// и не было. Это худший случай из всех: выглядит покрытой, не будучи ею.
	Kind string `json:"kind,omitempty"`
}

// CoverageReport — то, что отдаёт --coverage.
type CoverageReport struct {
	Total    int `json:"total"`
	Working  int `json:"working"`
	Excused  int `json:"excused"`
	Uncoverd int `json:"uncovered"`

	Gaps []CoverageGap `json:"gaps"`
}

// excusedClasses — классы, освобождающие площадку от инструмента.
//
// Список закрытый и короткий, и это существенно: у каждого класса причина в
// самой площадке, а не в нашей неготовности. Клон описывает те же залы, что
// другая запись; закрытая не работает по словам источника; у площадки без
// сущности «сеанс» расписания нет в природе.
//
// Классов нашей неготовности здесь нет намеренно — ни uncovered («адаптер не
// написан»), ни site_unknown, ни geo_unknown. Отдельно нет no_source: потерянный
// домен означает искать другой канал, а не списывать площадку.
var excusedClasses = map[string]bool{
	classCloneOf:      true,
	classClosed:       true,
	classNoOnlineSale: true,
}

// coverage считает покрытие по реестру.
func coverage(obs []CinemaObservation) CoverageReport {
	rep := CoverageReport{Total: len(obs)}

	for _, o := range obs {
		class := o.Fields[fStatusClass]

		// Живой ответ канала. Единственное доказательство работающего
		// инструмента, и его нельзя проставить себе — пишет только опрос.
		if o.Fields[fLastOk] != "" {
			rep.Working++
			continue
		}

		if excusedClasses[class] {
			// Освобождение без улики — та же самоназначенная пометка, поэтому
			// оно не засчитывается. У клона уликой служит ссылка на ведущую
			// запись, у закрытой — слова сети, у площадки без сеансов — причина
			// словами.
			if excuseEvidence(o) == "" {
				rep.Uncoverd++
				rep.Gaps = append(rep.Gaps, CoverageGap{
					Key: o.Key, Name: o.Name,
					Reason: "освобождение «" + class + "» без улики источника",
				})
				continue
			}
			rep.Excused++
			continue
		}

		rep.Uncoverd++
		rep.Gaps = append(rep.Gaps, CoverageGap{
			Key:    o.Key,
			Name:   o.Name,
			Kind:   o.Fields[fSourceKind],
			Reason: gapReason(o),
		})
	}

	sort.Slice(rep.Gaps, func(i, j int) bool { return rep.Gaps[i].Name < rep.Gaps[j].Name })
	return rep
}

// excuseEvidence — чем подтверждено освобождение площадки.
func excuseEvidence(o CinemaObservation) string {
	for _, f := range []string{fEvidenceURL, fLastError, fSourceParams} {
		if v := strings.TrimSpace(o.Fields[f]); v != "" {
			return v
		}
	}
	return ""
}

// gapReason объясняет, чего площадке не хватает.
//
// Различать эти случаи обязательно: «канал не назначен» — работа впереди, а
// «канал назначен, живого ответа нет» — работа сделана неверно, и площадка при
// этом выглядит покрытой в любом отчёте по видам каналов.
func gapReason(o CinemaObservation) string {
	if o.Fields[fSourceKind] != "" {
		if s := o.Fields[fLastStatus]; s != "" {
			return "канал назначен, живого ответа нет: " + s
		}
		return "канал назначен, но опрос по нему ещё не проходил"
	}
	if e := strings.TrimSpace(o.Fields[fLastError]); e != "" {
		return "канала нет: " + e
	}
	if c := o.Fields[fStatusClass]; c != "" {
		return "канала нет: " + c
	}
	return "канал не назначен"
}

// runCoverage печатает недостачу и падает, пока она есть.
//
// Ненулевой код — не украшение: это единственный способ сделать готовность
// проверяемой снаружи. Пока команда красная, работа не закончена, и мнение
// агента об этом ничего не меняет.
func runCoverage(short bool) {
	obs, err := readRegistry(os.Stdin)
	if err != nil {
		fail("%v", err)
	}

	rep := coverage(obs)
	if short {
		fmt.Printf("работает %d из %d, освобождено %d, осталось %d\n",
			rep.Working, rep.Total, rep.Excused, rep.Uncoverd)
	} else {
		out, err := json.MarshalIndent(rep, "", "  ")
		if err != nil {
			fail("сериализация: %v", err)
		}
		fmt.Println(string(out))
	}

	if rep.Uncoverd > 0 {
		fail("без работающего инструмента %d площадок из %d", rep.Uncoverd, rep.Total)
	}
}
