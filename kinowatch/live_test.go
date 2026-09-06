package main

// Живая проверка починенных каналов. Не для CI: ходит в настоящие кассы.
// Запуск: go test -run TestLiveDayChannels -v -tags= .   (KINOWATCH_LIVE=1)

import (
	"os"
	"testing"
	"time"
)

func TestLiveDayChannels(t *testing.T) {
	if os.Getenv("KINOWATCH_LIVE") == "" {
		t.Skip("живой прогон: KINOWATCH_LIVE=1")
	}
	c := newClient(60, 2)
	now := time.Now().In(moscowTZ)

	cases := []struct {
		name   string
		kind   string
		params ChannelParams
	}{
		{"Мираж ОТРАДНОЕ", kindMirage, ChannelParams{pVenue: "23"}},
		{"кинотеатр «Москва»", kindMoskva, ChannelParams{}},
		{"Mori Кунцево", kindMori, ChannelParams{pVenue: "1"}},
		{"ГУМ", kindGum, ChannelParams{}},
	}

	for _, tc := range cases {
		probe := fetchChannel(c, tc.kind, tc.params, now, 10)
		t.Logf("%s: сеансов=%d горизонт_источника=%v окно=%s..%s отказов=%d слепота=%q err=%v parse=%v",
			tc.name, len(probe.Playbill.Showtimes), probe.Playbill.SourceDays,
			probe.WindowFrom, probe.WindowTo, len(probe.FailedDays),
			probe.DateBlind, probe.Err, probe.ParseErr)

		days := showtimeDays(probe.Playbill)
		t.Logf("%s: дни сеансов %v", tc.name, days)
		if len(probe.Playbill.Showtimes) > 0 && len(days) < 2 {
			t.Errorf("%s: сеансы всего одного дня — канал не различает даты", tc.name)
		}
	}
}
