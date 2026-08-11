// Траектория движения мыши: перенос четырёх функций ghost-cursor (MIT) своим кодом.
//
// Зачем перенос, а не зависимость: скрипт уходит в песочницу QuickJS внутри dev-browser,
// где нет ни require, ни import, ни node_modules — только текст программы. Раньше сюда
// приклеивался собранный esbuild-ом бандл на 54 КБ; читать и ревьюить его было нельзя.
//
// Перенос дословный, включая порядок арифметики и константы квадратуры: при одинаковом
// потоке Math.random результат совпадает с библиотекой до последнего бита. Отсюда
// требование к правкам: менять формулы нельзя, иначе теряется то самое поведение, ради
// которого библиотека и бралась. Равенство проверяется тестом рядом с этим файлом.
//
// Внутри — только чистая математика: ничего про браузер, страницу и события.
var GC = (function () {
  // --- вектор ------------------------------------------------------------------------
  var sub = function (a, b) { return { x: a.x - b.x, y: a.y - b.y }; };
  var div = function (a, b) { return { x: a.x / b, y: a.y / b }; };
  var mult = function (a, b) { return { x: a.x * b, y: a.y * b }; };
  var add = function (a, b) { return { x: a.x + b.x, y: a.y + b.y }; };
  var direction = function (a, b) { return sub(b, a); };
  var perpendicular = function (a) { return { x: a.y, y: -1 * a.x }; };
  var magnitude = function (a) { return Math.sqrt(Math.pow(a.x, 2) + Math.pow(a.y, 2)); };
  var unit = function (a) { return div(a, magnitude(a)); };
  var setMagnitude = function (a, amount) { return mult(unit(a), amount); };
  var clamp = function (target, min, max) { return Math.min(max, Math.max(min, target)); };

  // --- случайности ---------------------------------------------------------------------
  var randomNumberRange = function (min, max) { return Math.random() * (max - min) + min; };

  var randomVectorOnLine = function (a, b) {
    var vec = direction(a, b);
    var multiplier = Math.random();
    return add(a, mult(vec, multiplier));
  };

  var randomNormalLine = function (a, b, range) {
    var randMid = randomVectorOnLine(a, b);
    var normalV = setMagnitude(perpendicular(direction(a, randMid)), range);
    return [randMid, normalV];
  };

  // Промах мимо цели: точка в круге радиуса radius вокруг координаты. Корень из
  // случайного числа даёт равномерность по площади, а не сгущение к центру.
  var overshoot = function (coordinate, radius) {
    var a = Math.random() * 2 * Math.PI;
    var rad = radius * Math.sqrt(Math.random());
    var vector = { x: rad * Math.cos(a), y: rad * Math.sin(a) };
    return add(coordinate, vector);
  };

  // --- кубическая кривая ---------------------------------------------------------------
  // Опорные точки кривой: две штуки по одну сторону от прямой старт-финиш, отсортированы
  // по x. Сторона выбирается монеткой — движение уходит то влево, то вправо.
  var generateBezierAnchors = function (a, b, spread) {
    var side = Math.round(Math.random()) === 1 ? 1 : -1;
    var calc = function () {
      var пара = randomNormalLine(a, b, spread);
      var randMid = пара[0], normalV = пара[1];
      var choice = mult(normalV, side);
      return randomVectorOnLine(randMid, add(randMid, choice));
    };
    return [calc(), calc()].sort(function (a, b) { return a.x - b.x; });
  };

  // Точка на кривой при параметре t. Раскрытие Бернштейна для четырёх точек.
  var compute = function (t, p) {
    if (t === 0) return { x: p[0].x, y: p[0].y };
    if (t === 1) return { x: p[3].x, y: p[3].y };
    var mt = 1 - t;
    var mt2 = mt * mt, t2 = t * t;
    var a = mt2 * mt;
    var b = mt2 * t * 3;
    var c = mt * t2 * 3;
    var d = t * t2;
    return {
      x: a * p[0].x + b * p[1].x + c * p[2].x + d * p[3].x,
      y: a * p[0].y + b * p[1].y + c * p[2].y + d * p[3].y,
    };
  };

  // Производная кубики — квадратичная кривая по трём точкам.
  var derive = function (p) {
    return [
      { x: 3 * (p[1].x - p[0].x), y: 3 * (p[1].y - p[0].y) },
      { x: 3 * (p[2].x - p[1].x), y: 3 * (p[2].y - p[1].y) },
      { x: 3 * (p[3].x - p[2].x), y: 3 * (p[3].y - p[2].y) },
    ];
  };

  var computeD = function (t, p) {
    if (t === 0) return { x: p[0].x, y: p[0].y };
    if (t === 1) return { x: p[2].x, y: p[2].y };
    var mt = 1 - t;
    var a = mt * mt;
    var b = mt * t * 2;
    var c = t * t;
    return {
      x: a * p[0].x + b * p[1].x + c * p[2].x,
      y: a * p[0].y + b * p[1].y + c * p[2].y,
    };
  };

  // Длина дуги считается численно — у кубической кривой замкнутой формулы нет.
  // Квадратура Гаусса-Лежандра на 24 узлах: абсциссы T и веса C — табличные константы.
  var Tvalues = [
    -0.0640568928626056260850430826247450385909, 0.0640568928626056260850430826247450385909,
    -0.1911188674736163091586398207570696318404, 0.1911188674736163091586398207570696318404,
    -0.3150426796961633743867932913198102407864, 0.3150426796961633743867932913198102407864,
    -0.4337935076260451384870842319133497124524, 0.4337935076260451384870842319133497124524,
    -0.5454214713888395356583756172183723700107, 0.5454214713888395356583756172183723700107,
    -0.6480936519369755692524957869107476266696, 0.6480936519369755692524957869107476266696,
    -0.7401241915785543642438281030999784255232, 0.7401241915785543642438281030999784255232,
    -0.8200019859739029219539498726697452080761, 0.8200019859739029219539498726697452080761,
    -0.8864155270044010342131543419821967550873, 0.8864155270044010342131543419821967550873,
    -0.9382745520027327585236490017087214496548, 0.9382745520027327585236490017087214496548,
    -0.9747285559713094981983919930081690617411, 0.9747285559713094981983919930081690617411,
    -0.9951872199970213601799974097007368118745, 0.9951872199970213601799974097007368118745,
  ];
  var Cvalues = [
    0.1279381953467521569740561652246953718517, 0.1279381953467521569740561652246953718517,
    0.1258374563468282961213753825111836887264, 0.1258374563468282961213753825111836887264,
    0.1216704729278033912044631534762624256070, 0.1216704729278033912044631534762624256070,
    0.1155056680537256013533444839067835598622, 0.1155056680537256013533444839067835598622,
    0.1074442701159656347825773424466062227946, 0.1074442701159656347825773424466062227946,
    0.0976186521041138882698806644642471544279, 0.0976186521041138882698806644642471544279,
    0.0861901615319532759171852029837426671850, 0.0861901615319532759171852029837426671850,
    0.0733464814110803057340336152531165181193, 0.0733464814110803057340336152531165181193,
    0.0592985849154367807463677585001085845412, 0.0592985849154367807463677585001085845412,
    0.0442774388174198061686027482113382288593, 0.0442774388174198061686027482113382288593,
    0.0285313886289336631813078159518782864491, 0.0285313886289336631813078159518782864491,
    0.0123412297999871995468056670700372915759, 0.0123412297999871995468056670700372915759,
  ];

  var curveLength = function (points) {
    var d = derive(points);
    var z = 0.5, len = Tvalues.length, sum = 0;
    for (var i = 0, t; i < len; i++) {
      t = z * Tvalues[i] + z;
      var dv = computeD(t, d);
      sum += Cvalues[i] * Math.sqrt(dv.x * dv.x + dv.y * dv.y);
    }
    return z * sum;
  };

  // Разбиение кривой на steps отрезков даёт steps+1 точек, крайние — ровно старт и финиш.
  var lut = function (points, steps) {
    var out = [];
    var n = steps + 1;
    for (var i = 0; i < n; i++) out.push(compute(i / (n - 1), points));
    return out;
  };

  var bezierPoints = function (start, finish, spreadOverride) {
    var MIN_SPREAD = 2;
    var MAX_SPREAD = 200;
    var vec = direction(start, finish);
    var length = magnitude(vec);
    var spread = spreadOverride !== null && spreadOverride !== undefined
      ? spreadOverride
      : clamp(length, MIN_SPREAD, MAX_SPREAD);
    var anchors = generateBezierAnchors(start, finish, spread);
    return [start, anchors[0], anchors[1], finish];
  };

  // Закон Фиттса: чем дальше цель и чем она меньше, тем дольше до неё добираются.
  var fitts = function (distance, width) {
    var a = 0;
    var b = 2;
    var id = Math.log2(distance / width + 1);
    return a + b * id;
  };

  // Путь от точки до точки: список точек по кривой. Число точек зависит от длины пути,
  // размера цели и случайной «скорости» — поэтому два одинаковых движения не совпадают.
  var path = function (start, end, options) {
    var opts = typeof options === "number" ? { spreadOverride: options } : (options || {});
    var DEFAULT_WIDTH = 100;
    var MIN_STEPS = 25;
    var width = "width" in end && end.width !== 0 ? end.width : DEFAULT_WIDTH;
    var points = bezierPoints(start, end, opts.spreadOverride);
    var length = curveLength(points) * 0.8;
    var speed = opts.moveSpeed !== undefined && opts.moveSpeed > 0
      ? (25 / opts.moveSpeed)
      : Math.random();
    var baseTime = speed * MIN_STEPS;
    var steps = Math.ceil((Math.log2(fitts(length, width) + 1) + baseTime) * 3);
    return lut(points, steps).map(function (v) {
      return { x: Math.max(0, v.x), y: Math.max(0, v.y) };
    });
  };

  return {
    path: path,
    overshoot: overshoot,
    randomNumberRange: randomNumberRange,
    randomVectorOnLine: randomVectorOnLine,
  };
})();
