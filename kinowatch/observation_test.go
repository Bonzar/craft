package main

import "testing"

// Приоритет классов — не косметика: он решает, попадёт ли площадка в
// знаменатель покрытия. Тест держит именно это свойство, а не порядок ради
// порядка.
func TestPickClassPriority(t *testing.T) {
	cases := []struct {
		name    string
		reasons []string
		want    string
	}{
		{"пусто", nil, ""},
		{"один класс", []string{classUncovered}, classUncovered},
		{
			"сайт не найден важнее ненаписанного адаптера",
			[]string{classUncovered, classSiteUnknown},
			classSiteUnknown,
		},
		{
			"мёртвый домен важнее несошедшегося геокода: он объективен",
			[]string{classNoSource, classGeoUnknown},
			classNoSource,
		},
		{
			"нет онлайн-продажи важнее ненаписанного адаптера",
			[]string{classUncovered, classNoOnlineSale},
			classNoOnlineSale,
		},
		{
			"сезонность важнее ненаписанного адаптера: вне сезона расписания нет ни при каком адаптере",
			[]string{classUncovered, classSeasonal},
			classSeasonal,
		},
		{"пустые причины игнорируются", []string{"", classSeasonal, ""}, classSeasonal},
	}

	for _, c := range cases {
		if got := pickClass(c.reasons...); got != c.want {
			t.Errorf("%s: pickClass(%v) = %q, want %q", c.name, c.reasons, got, c.want)
		}
	}
}

// Знаменатель покрытия обязан сохранять всё, что про НАШУ неготовность.
// Иначе процент считался бы от удобной выборки: не нашли сайт — выкинули из
// знаменателя — покрытие 100%.
func TestKeepsInDenominator(t *testing.T) {
	keep := []string{classSiteUnknown, classGeoUnknown, classUncovered, ""}
	drop := []string{classNoSource, classNoOnlineSale, classSeasonal}

	for _, c := range keep {
		if !keepsInDenominator(c) {
			t.Errorf("класс %q должен оставаться в знаменателе: он про нашу неготовность", c)
		}
	}
	for _, c := range drop {
		if keepsInDenominator(c) {
			t.Errorf("класс %q не должен считаться в знаменателе: он про саму площадку", c)
		}
	}
}

// Объективное препятствие всегда сильнее нашей неготовности.
//
// Смысл в честности метрики в обе стороны: площадку, которую нельзя опросить в
// принципе (мёртвый домен, музей без билетной системы, закрытый до лета
// летний кинотеатр), незачем держать в знаменателе — покрытие иначе никогда не
// сойдётся по причинам вне нашего контроля. И наоборот: пока препятствие наше,
// площадка обязана остаться в знаменателе и мозолить глаза.
func TestObjectiveReasonsOutrankOurOwnGaps(t *testing.T) {
	objective := []string{classNoSource, classNoOnlineSale, classSeasonal}
	ours := []string{classSiteUnknown, classGeoUnknown, classUncovered}

	for _, o := range objective {
		for _, m := range ours {
			got := pickClass(m, o)
			if got != o {
				t.Errorf("совпали наше %q и объективное %q → выбрано %q, должно быть %q",
					m, o, got, o)
			}
			if keepsInDenominator(got) {
				t.Errorf("объективная причина %q обязана выводить площадку из знаменателя", got)
			}
		}
	}

	// Обратная сторона: если объективных препятствий нет, побеждает наше — и
	// площадка остаётся в знаменателе как невыполненная работа.
	for i := range ours {
		for j := range ours {
			if got := pickClass(ours[i], ours[j]); !keepsInDenominator(got) {
				t.Errorf("совпали %q и %q → выбран %q, площадка выпала из знаменателя",
					ours[i], ours[j], got)
			}
		}
	}
}

func TestBuildCinemaObservations(t *testing.T) {
	decisions := []ScopeDecision{
		{Row: EaisRow{ID: "6038", Company: "Иллюзион", City: "Москва г"}, InScope: true},
		{Row: EaisRow{ID: "1923", Company: "Киностар Де Люкс", City: "Мамыри д", Network: "СИНЕМА ПАРК"},
			InScope: false, Reason: "tinao"},
		{Row: EaisRow{ID: "8656", Company: "КАРО под звёздами", City: "Москва г", Network: "КАРО ФИЛЬМ"},
			InScope: true},
	}

	obs := buildCinemaObservations(decisions, "2026-07-31T10:00:00Z")

	if len(obs) != 2 {
		t.Fatalf("наблюдений %d, ожидалось 2 (строка вне охвата в реестр не пишется)", len(obs))
	}

	if obs[0].Key != "6038" || obs[0].Name != "Иллюзион" {
		t.Errorf("первое наблюдение: key=%q name=%q", obs[0].Key, obs[0].Name)
	}

	// Ключи свойств — схлопнутые, без подчёркиваний: Craft их стирает.
	if _, ok := obs[0].Fields["statusclass"]; !ok {
		t.Errorf("ключ статуса должен быть схлопнутым 'statusclass', получено: %v", obs[0].Fields)
	}

	// Пустая площадка не молчит: она сразу непокрытая, а значит видна в
	// знаменателе как наша недоработка.
	if got := obs[0].Fields[fStatusClass]; got != classUncovered {
		t.Errorf("свежая площадка должна получить %q, получено %q", classUncovered, got)
	}
	if !keepsInDenominator(obs[0].Fields[fStatusClass]) {
		t.Error("свежая площадка обязана остаться в знаменателе покрытия")
	}

	if got := obs[1].Fields[fNetwork]; got != "КАРО ФИЛЬМ" {
		t.Errorf("сеть не перенесена: %q", got)
	}
}
