// The empty task field's placeholder speaks: a line is typed out, stays, is erased in quick bursts, and another one
// follows. Some lines stay for minutes and some only seconds, so the field looks alive without flickering.
// A line about now (the hour, the day, the date, the person's tasks) comes up every other time while it holds,
// three lines to each, so the same thing is never said twice in a visit and nothing reads as one canned sentence.
//
// The same file is cloud/mobile/js/hints.js and cloud/workspace/js/hints.js (a test keeps them equal): the two pages
// are plain files with no bundler, served from different roots.

const line = (en, ru) => ({ en, ru });

export const GENERAL = [
  line("Let's get to work", "Давайте начнём работу"),
  line("I'm always here", "Я всегда тут"),
  line("Let's automate it", "Давайте автоматизировать"),
  line("Glad to see you", "Рада вас видеть"),
  line("A bold idea, I must say", "Смелая идея, однако"),
  line("Kvist", "Kvist"),
  line("Let's build", "Давайте строить"),
  line("I'll try to finish faster (no promises)", "Постараюсь закончить задачу побыстрее (но это не точно)"),
  line("Where do we start?", "С чего начнём?"),
  line("Tell me what's in the way, I'll sort it out", "Расскажите, что мешает, — разберусь"),
  line("Hand the routine to me", "Скиньте рутину на меня"),
  line("What shall I do for you today?", "Что сегодня сделаем за вас?"),
  line("Describe it as it is, I'll understand", "Опишите как есть, я пойму"),
  line("A big task or a small one", "Можно большую задачу. Можно маленькую"),
  line("I don't get tired. Honestly", "Я не устаю. Честно"),
  line("Try something crazy", "Попробуйте что-нибудь безумное"),
  line("Kvist is listening", "Kvist слушает"),
  line("One task, and things get lighter", "Одна задача — и станет легче"),
  line("The more specific, the faster", "Чем конкретнее, тем быстрее"),
  line("Let's do what you never get round to", "Давайте сделаем то, до чего руки не доходят"),
  line("I've warmed up already", "Я уже размялась"),
  line("Show me what doesn't work", "Покажите, что не работает"),
  line("Got an idea? Let's start with it", "Идея есть? Начнём с неё"),
];

const DAY_MS = 86_400_000;
const hour = (f) => f.now.getHours();
const weekday = (f) => f.now.getDay();
const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
const dayOfYear = (date) => Math.round((new Date(date.getFullYear(), date.getMonth(), date.getDate()) - new Date(date.getFullYear(), 0, 1)) / DAY_MS) + 1;
const when = (task) => new Date(task.finished_at ?? task.started_at ?? task.created_at ?? 0);
const createdOn = (task, day) => Boolean(task.created_at) && sameDay(new Date(task.created_at), day);

/** The newest task that ended by itself: a stop the person made says nothing about how the work went. */
function lastEnded(tasks) {
  const ended = tasks.filter((task) => task.status === "done" || task.status === "failed");
  ended.sort((a, b) => when(b) - when(a));
  return ended[0] ?? null;
}

function anniversary(f) {
  if (!f.createdAt) return false;
  const since = new Date(f.createdAt);
  return f.now.getFullYear() > since.getFullYear() && f.now.getMonth() === since.getMonth() && f.now.getDate() === since.getDate();
}

function trialEndsSoon(f) {
  if (f.sub?.status !== "trialing" || !f.sub.trial_ends_at) return false;
  const left = new Date(f.sub.trial_ends_at) - f.now;
  return left > 0 && left <= DAY_MS;
}

function holidays(f) {
  const month = f.now.getMonth();
  const date = f.now.getDate();
  return (month === 11 && date >= 25 && date !== 31) || (month === 0 && date <= 8);
}

export const CONDITIONS = [
  { id: "morning", when: (f) => hour(f) >= 6 && hour(f) < 11, lines: [
    line("Good morning. Coffee's on you, tasks are on me", "Доброе утро. Кофе за вами, задачи за мной"),
    line("Morning is the best time to start", "Утро — лучшее время начать"),
    line("I'm awake already. Are you?", "Я уже проснулась. А вы?")] },
  { id: "lunch", when: (f) => hour(f) >= 12 && hour(f) < 14, lines: [
    line("Go have lunch, I'll work meanwhile", "Идите обедать, я пока поработаю"),
    line("Leave a task and eat in peace", "Оставьте задачу и спокойно поешьте"),
    line("Lunch is a good reason to hand me something", "Обед — повод что-нибудь мне поручить")] },
  { id: "evening", when: (f) => hour(f) >= 18 && hour(f) < 23, lines: [
    line("Leave a task, it'll be ready by morning", "Оставьте задачу, утром будет готово"),
    line("The day is ending, I'm not", "День заканчивается, а я нет"),
    line("Rest, I'll take the evening", "Отдыхайте, вечер я возьму на себя")] },
  { id: "night", when: (f) => hour(f) < 5, lines: [
    line("Can't sleep? Me neither", "Не спится? Мне тоже"),
    line("Work is quieter at night", "Ночью работается тише"),
    line("Let me do it while you sleep", "Давайте сделаю, а вы поспите")] },
  { id: "monday", when: (f) => weekday(f) === 1, lines: [
    line("Shall we start what we put off till Monday?", "Надеюсь, начнём то, что откладывали до понедельника?"),
    line("Monday. Time to keep those promises", "Понедельник. Обещания пора выполнять"),
    line("A new week, new tasks", "Новая неделя, новые задачи")] },
  { id: "friday", when: (f) => weekday(f) === 5 && f.now.getDate() !== 13, lines: [
    line("Friday. Let's finish and rest", "Пятница. Давайте закончим и отдыхать"),
    line("Shall we get it all done before the weekend?", "Сделаем всё до выходных?"),
    line("The week's last push", "Последний рывок недели")] },
  { id: "weekend", when: (f) => weekday(f) === 0 || weekday(f) === 6, lines: [
    line("Weekends work too. I won't tell anyone", "В выходные тоже можно. Я никому не скажу"),
    line("Rest, I'll do it myself", "Отдыхайте, я сама"),
    line("Saturday is a good day for your own projects", "Суббота — хороший день для своих проектов")] },
  { id: "friday 13", when: (f) => weekday(f) === 5 && f.now.getDate() === 13, lines: [
    line("Friday the 13th. I'm not superstitious", "Пятница, 13-е. Я не суеверная"),
    line("Everything will go smoothly today. Probably", "Сегодня всё пройдёт гладко. Наверное"),
    line("Black cats don't affect code", "Чёрные кошки на код не влияют")] },
  { id: "first visit", when: (f) => f.firstVisit, lines: [
    line("Hi! I'm Kvist. Give me your first task", "Привет! Я Kvist. Дайте мне первую задачу"),
    line("Nice to meet you. Where do we start?", "Рада знакомству. С чего начнём?"),
    line("The first task is the most interesting one", "Первая задача — самая интересная")] },
  { id: "back after a week", when: (f) => f.awayDays >= 7, lines: [
    line("Long time no see", "Давно не виделись"),
    line("I missed you. A little", "Я скучала. Немного"),
    line("Welcome back! What's new?", "С возвращением! Что нового?")] },
  { id: "unfinished", when: (f) => f.tasks.some((task) => task.status === "paused" || task.status === "waiting_for_user"), lines: [
    line("Shall we pick up where we left off?", "Продолжим, где остановились?"),
    line("Something there is waiting to be finished", "Там кое-что ждёт окончания"),
    line("Back to the last task?", "Вернёмся к прошлой задаче?")] },
  { id: "last went well", when: (f) => lastEnded(f.tasks)?.status === "done", lines: [
    line("That went well last time. More?", "Хорошо вышло в прошлый раз. Ещё?"),
    line("The last task is done. Next?", "Прошлая задача готова. Следующая?"),
    line("I'm warmed up, let's go on", "Разогрелась — давайте дальше")] },
  { id: "last failed", when: (f) => lastEnded(f.tasks)?.status === "failed", lines: [
    line("It didn't work last time. Try another way?", "В прошлый раз не вышло. Попробуем иначе?"),
    line("Mistakes are part of the way", "Ошибки — это тоже путь"),
    line("Give me a second chance", "Дайте мне второй шанс")] },
  { id: "third today", when: (f) => f.tasks.filter((task) => createdOn(task, f.now)).length >= 3, lines: [
    line("A productive day we're having", "Продуктивный день у нас"),
    line("The third one today. Keep going?", "Третья за сегодня. Не останавливаемся?"),
    line("We're on a roll today", "Мы сегодня в ударе")] },
  { id: "busy week", when: (f) => f.tasks.filter((task) => task.created_at && f.now - new Date(task.created_at) < 7 * DAY_MS).length >= 10, lines: [
    line("We've had a great week", "Отличная неделя у нас вышла"),
    line("You keep me busy. I like it", "Вы меня загрузили. Мне нравится"),
    line("We make a great team", "Мы отличная команда")] },
  { id: "a year together", when: anniversary, lines: [
    line("A year together. Thank you for staying", "Год вместе. Спасибо, что вы со мной"),
    line("Today is our anniversary", "Сегодня наш с вами год"),
    line("A year of tasks behind us. More ahead", "Год задач позади. Впереди больше")] },
  { id: "trial ends", when: trialEndsSoon, lines: [
    line("The trial is ending. Time for one more?", "Пробный период заканчивается. Успеем ещё что-нибудь?"),
    line("The trial's last day. Let's do something big", "Последний день пробного периода. Давайте сделаем что-то большое"),
    line("I'll show all I can while there's time", "Покажу всё, на что способна, пока есть время")] },
  { id: "phone", when: (f) => f.phone, lines: [
    line("You can do it right from bed", "Можно прямо из кровати"),
    line("A couple of words and the work is on", "Пара слов — и работа пошла"),
    line("One finger is enough for a task", "Задачу можно и одним пальцем")] },
  { id: "typed and erased", when: (f) => f.erasedAt !== null && f.now - f.erasedAt < 5 * 60_000, lines: [
    line("Write it as it is, I'll understand", "Пишите как есть, я пойму"),
    line("Don't be shy, write", "Не стесняйтесь, пишите"),
    line("A rough draft will do", "Черновой вариант тоже подойдёт")] },
  { id: "thinking", when: (f) => !f.typed && f.pageMs >= 2 * 60_000, lines: [
    line("Thinking? I'll wait", "Думаете? Я подожду"),
    line("You can start with one word", "Можно начать с одного слова"),
    line("Not sure where to start? Ask me", "Не знаете, с чего начать? Спросите меня")] },
  { id: "long stay", when: (f) => f.pageMs >= 2 * 3_600_000, lines: [
    line("Maybe a break? I'll keep working", "Может, перерыв? Я поработаю"),
    line("You've been here a while. Drink some water", "Вы давно тут. Не забудьте попить воды"),
    line("Get up, stretch, I'm here", "Встаньте, разомнитесь, я тут")] },
  { id: "holidays", when: holidays, lines: [
    line("Holidays are a good time for your own ideas", "Праздники — хорошее время для своих идей"),
    line("While everyone rests, we build", "Пока все отдыхают, мы строим"),
    line("The tangerines are yours, the tasks are mine", "Мандарины ваши, задачи мои")] },
  { id: "new year's eve", when: (f) => f.now.getMonth() === 11 && f.now.getDate() === 31, lines: [
    line("Shall we close the year with nothing owed?", "Закроем год без долгов?"),
    line("The last task of the year?", "Последняя задача года?"),
    line("We'll make it before the New Year", "До Нового года успеем")] },
  { id: "april 1", when: (f) => f.now.getMonth() === 3 && f.now.getDate() === 1, lines: [
    line("I'm joking today. But working for real", "Сегодня шучу. Но работаю всерьёз"),
    line("Not a prank, I'm really ready", "Это не розыгрыш, я правда готова"),
    line("Give me a task. No jokes", "Дайте задачу. Без шуток")] },
  { id: "programmers' day", when: (f) => dayOfYear(f.now) === 256, lines: [
    line("Happy Programmers' Day! I write the code today", "С днём программиста! Сегодня код пишу я"),
    line("Day 256. A beautiful number", "256-й день. Красивое число"),
    line("Rest, it's your day today", "Отдыхайте, сегодня ваш день")] },
  { id: "september 1", when: (f) => f.now.getMonth() === 8 && f.now.getDate() === 1, lines: [
    line("The school year has begun. What shall we learn?", "Учебный год начался. Чему научимся?"),
    line("September the first is a good start", "Первое сентября — хороший старт"),
    line("Time for new beginnings", "Время новых начинаний")] },
  { id: "first of the month", when: (f) => f.now.getDate() === 1 && f.now.getMonth() !== 8, lines: [
    line("A new month, a new plan", "Новый месяц, новый план"),
    line("Shall we start the month with what matters?", "Начнём месяц с главного?"),
    line("The first is a reason to start over", "Первое число — повод начать заново")] },
];

const LONG_MS = [120_000, 180_000];
const SHORT_MS = [15_000, 25_000];
const LONG_SHARE = 0.4;
const SHORTS_IN_A_ROW = 2;

const between = (random, [low, high]) => low + Math.round(random() * (high - low));

function shuffled(items, random) {
  const copy = [...items];
  for (let i = copy.length - 1; i > 0; i -= 1) {
    const j = Math.floor(random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

/**
 * Which line comes next and how long it stays. A condition's three lines are each said once a visit; the general
 * ones go round in a new order every time the deck is used up. `fits(line)` says whether a line fits the field in
 * one row: the placeholder does not wrap, and a line cut by an ellipsis loses its point.
 */
export function createPicker(random = Math.random) {
  const decks = new Map(CONDITIONS.map((condition) => [condition.id, shuffled(condition.lines, random)]));
  let general = [];
  let count = 0;
  let shortRun = 0;
  let lastWasCondition = false;
  let turn = 0;

  const take = (deck, fits) => {
    const index = deck.findIndex(fits);
    return index < 0 ? null : deck.splice(index, 1)[0];
  };
  const fromCondition = (facts, fits) => {
    const live = CONDITIONS.filter((condition) => decks.get(condition.id).some(fits) && condition.when(facts));
    if (!live.length) return null;
    turn += 1;
    return take(decks.get(live[turn % live.length].id), fits);
  };
  const fromGeneral = (fits) => {
    if (!general.length) general = shuffled(GENERAL, random);
    const found = take(general, fits);
    if (found) return found;
    general = shuffled(GENERAL, random);
    // A field too narrow for every line still gets one, cut short, rather than none.
    return take(general, fits) ?? general.shift();
  };
  const holdFor = (isCondition) => {
    if (count === 1 || isCondition || shortRun >= SHORTS_IN_A_ROW || random() < LONG_SHARE) {
      shortRun = 0;
      return between(random, LONG_MS);
    }
    shortRun += 1;
    return between(random, SHORT_MS);
  };

  return {
    next(facts, fits = () => true) {
      count += 1;
      const special = lastWasCondition ? null : fromCondition(facts, fits);
      lastWasCondition = Boolean(special);
      return { line: special ?? fromGeneral(fits), hold: holdFor(Boolean(special)) };
    },
  };
}

const TYPE_MS = [45, 55];
const PAUSE_AFTER = { ",": 150, ";": 150, "—": 150, ".": 300, "!": 300, "?": 300, ")": 300 };
const ERASE_CHAR_MS = 12;
const ERASE_BURST = [2, 4];
const ERASE_GAP_MS = [60, 140];
const BLANK_MS = 250;
const CARET = "▏";

/** The waits between letters as a line is typed out: a person's rhythm, slower after a comma, slower still after a full stop. */
export function typingDelays(text, random = Math.random) {
  const chars = [...text];
  return chars.map((_, index) => between(random, TYPE_MS) + (PAUSE_AFTER[chars[index - 1]] ?? 0));
}

/** How a line is erased: bursts of a few letters, quick inside, with a short stop between them. */
export function eraseBursts(length, random = Math.random) {
  const bursts = [];
  for (let left = length; left > 0;) {
    const size = Math.min(left, between(random, ERASE_BURST));
    bursts.push({ size, gap: between(random, ERASE_GAP_MS) });
    left -= size;
  }
  return bursts;
}

const VISIT_KEY = "kvist.hints.seen";

/** When this browser last showed the field: the first visit and a return after a long break are told from it. */
function readVisit(now) {
  const stored = Number(localStorage.getItem(VISIT_KEY));
  localStorage.setItem(VISIT_KEY, String(now.getTime()));
  return stored ? { firstVisit: false, awayDays: (now - stored) / DAY_MS } : { firstVisit: true, awayDays: 0 };
}

/**
 * Makes the empty field's placeholder speak. `account()` gives {tasks, sub, createdAt}; `language()` "en" or "ru".
 * Returns {start, stop}: the page starts it while the field is the one that starts a task, and stops it otherwise
 * (then it puts its own placeholder back).
 */
export function mountHints(field, { account, language }) {
  const opened = new Date();
  // A finger rather than a mouse: a phone or a tablet, whichever of the two pages is open on it.
  const phone = matchMedia("(pointer: coarse)").matches;
  const visit = readVisit(opened);
  const picker = createPicker();
  const still = matchMedia("(prefers-reduced-motion: reduce)");
  let typed = false;
  let erasedAt = null;
  let running = false;
  let run = 0;
  // The whole line on screen, even while it is still being typed or erased: a pause shows it complete.
  let shown = "";
  let fallback = field.placeholder;

  const facts = () => {
    const now = new Date();
    const { tasks = [], sub = null, createdAt = "" } = account();
    return { now, ...visit, tasks, sub, createdAt, phone, typed, erasedAt, pageMs: now - opened };
  };
  const show = (text) => {
    shown = text;
    field.placeholder = text;
  };
  const ruler = document.createElement("canvas").getContext("2d");
  const fits = (candidate) => {
    const style = getComputedStyle(field);
    const room = field.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    // From its parts: Firefox leaves the `font` shorthand of a computed style empty.
    ruler.font = `${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
    return room <= 0 || ruler.measureText(`${candidate[language()] ?? candidate.en}${CARET}`).width <= room;
  };
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  // The person's own touch or key, not the focus: the page puts the cursor in the field by itself on a desktop.
  let engaged = false;
  const quiet = () => engaged || field.value || document.hidden;

  async function typeOut(text, mine) {
    if (still.matches) return show(text);
    shown = text;
    const chars = [...text];
    const delays = typingDelays(text);
    for (let index = 0; index < chars.length; index += 1) {
      await wait(delays[index]);
      if (mine !== run) return undefined;
      field.placeholder = chars.slice(0, index + 1).join("") + (index < chars.length - 1 ? CARET : "");
    }
    return show(text);
  }

  async function eraseOut(mine) {
    if (still.matches) return show("");
    const chars = [...shown];
    for (const burst of eraseBursts(chars.length)) {
      for (let i = 0; i < burst.size; i += 1) {
        await wait(ERASE_CHAR_MS);
        if (mine !== run) return undefined;
        chars.pop();
        field.placeholder = chars.length ? chars.join("") + CARET : "";
      }
      await wait(burst.gap);
      if (mine !== run) return undefined;
    }
    return show("");
  }

  /** One run of lines until something stops it; `firstHold` is what is left of the line already standing. */
  async function speak(firstHold) {
    run += 1;
    const mine = run;
    if (shown && firstHold) {
      await wait(firstHold);
      if (mine !== run) return;
    }
    while (mine === run) {
      if (shown) await eraseOut(mine);
      if (mine !== run) return;
      await wait(BLANK_MS);
      if (mine !== run) return;
      const { line: next, hold } = picker.next(facts(), fits);
      await typeOut(next[language()] ?? next.en, mine);
      if (mine !== run) return;
      await wait(hold);
    }
  }

  // Looking at the field or typing in it, the person reads the whole line, standing still.
  const freeze = () => {
    run += 1;
    if (running) field.placeholder = shown || fallback;
  };
  const resume = (hold) => {
    if (running && !quiet()) speak(hold);
  };

  const engage = () => {
    engaged = true;
    freeze();
  };
  field.addEventListener("pointerdown", engage);
  field.addEventListener("keydown", engage);
  field.addEventListener("blur", () => {
    const was = engaged;
    engaged = false;
    if (was) resume(between(Math.random, SHORT_MS));
  });
  field.addEventListener("input", () => {
    if (field.value) typed = true;
    else if (typed) erasedAt = new Date();
    freeze();
  });
  // Hidden, the timers stop; back on the page, a new line is typed while the eyes are on it.
  document.addEventListener("visibilitychange", () => (document.hidden ? freeze() : resume(0)));

  return {
    start() {
      if (running) return;
      running = true;
      fallback = field.placeholder;
      if (quiet()) return;
      speak(0);
    },
    stop() {
      running = false;
      run += 1;
      shown = "";
    },
  };
}
