package main

// Тесты счёта покрытия.
//
// Проверяется главное свойство: покрытие нельзя назначить себе пометкой. Каждый
// кейс ниже — попытка выдать непокрытую площадку за покрытую тем способом,
// который у агента под рукой.

import "testing"

func obsWith(name string, fields map[string]string) CinemaObservation {
	return CinemaObservation{Key: name, Name: name, Fields: fields}
}

// Проставленный вид канала покрытием не считается: он говорит, куда стучаться,
// а не то, что оттуда ответили.
func TestCoverageIgnoresAssignedKindWithoutLiveAnswer(t *testing.T) {
	rep := coverage([]CinemaObservation{
		obsWith("КАРО 6 Щука", map[string]string{fSourceKind: kindKaro, fSourceParams: "venue=12"}),
	})

	if rep.Working != 0 {
		t.Errorf("покрытой признана площадка без живого ответа: %+v", rep)
	}
	if rep.Uncoverd != 1 || len(rep.Gaps) != 1 {
		t.Fatalf("недостача посчитана неверно: %+v", rep)
	}
	if rep.Gaps[0].Kind != kindKaro {
		t.Error("в недостаче не видно, что канал назначен, — этот случай хуже пустого")
	}
}

// Живой ответ канала — единственное доказательство. Его пишет опрос.
func TestCoverageCountsLiveAnswer(t *testing.T) {
	rep := coverage([]CinemaObservation{
		obsWith("КАРО 6 Щука", map[string]string{
			fSourceKind: kindKaro, fLastOk: "2026-08-03T10:00:00Z", fLastStatus: statusAbsent,
		}),
	})
	if rep.Working != 1 || rep.Uncoverd != 0 {
		t.Errorf("живой ответ не засчитан: %+v", rep)
	}
}

// Освобождение без улики — та же самоназначенная пометка.
func TestCoverageRejectsExcuseWithoutEvidence(t *testing.T) {
	rep := coverage([]CinemaObservation{
		obsWith("Берёзка", map[string]string{fStatusClass: classClosed}),
	})
	if rep.Excused != 0 || rep.Uncoverd != 1 {
		t.Fatalf("голая пометка класса засчитана освобождением: %+v", rep)
	}
}

func TestCoverageAcceptsExcuseWithEvidence(t *testing.T) {
	rep := coverage([]CinemaObservation{
		obsWith("Берёзка", map[string]string{
			fStatusClass: classClosed,
			fEvidenceURL: "справочник сети, площадка «Берёзка (временно закрыт на ремонт)»",
		}),
	})
	if rep.Excused != 1 || rep.Uncoverd != 0 {
		t.Errorf("улика источника не засчитана: %+v", rep)
	}
}

// Классы нашей неготовности освобождением не являются — ни один.
//
// Отдельно важен no_source: соблазн списать площадку с потерянным доменом
// велик, но потерянный домен означает искать другой канал.
func TestCoverageNeverExcusesOurOwnGaps(t *testing.T) {
	for _, class := range []string{classUncovered, classSiteUnknown, classGeoUnknown, classNoSource, classSeasonal} {
		rep := coverage([]CinemaObservation{
			obsWith("площадка", map[string]string{
				fStatusClass: class,
				fEvidenceURL: "какая-то улика",
			}),
		})
		if rep.Uncoverd != 1 {
			t.Errorf("класс %q засчитан освобождением: %+v", class, rep)
		}
	}
}
