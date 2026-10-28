export type StarterIcon = "explain" | "review" | "tests" | "collect" | "compare" | "sort" | "routine" | "clean" | "fetch" | "site" | "tool" | "design"

export interface Starter {
  icon: StarterIcon
  title: string
  hint: string
  // What lands in the composer. Whatever the request still needs (which files, which address) the prompt
  // tells the agent to ask for, so the person answers questions instead of finishing a sentence with a hole in it.
  prompt: string
}

export interface StarterGroup {
  id: "code" | "documents" | "automation" | "build"
  label: string
  starters: Starter[]
}

// Grouped by the result a person wants, not by what the agent does to get there. The document
// prompts ask for the source of every figure because nobody can sign off on a number they cannot trace.
export function starterGroups(russian: boolean): StarterGroup[] {
  const t = (en: string, ru: string) => (russian ? ru : en)
  return [
    {
      id: "code",
      label: t("Code", "Код"),
      starters: [
        {
          icon: "explain",
          title: t("Explain this project", "Объяснить проект"),
          hint: t("What is here and where to start", "Что здесь есть и с чего начать"),
          prompt: t(
            "Explain this project in plain language: what it does, how it is organized and where to start.",
            "Объясни этот проект простым языком: что он делает, как устроен и с чего начать.",
          ),
        },
        {
          icon: "review",
          title: t("Review my changes", "Проверить мои изменения"),
          hint: t("Bugs and risks before you commit", "Ошибки и риски до коммита"),
          prompt: t(
            "Review my uncommitted changes. Point out bugs, risks and anything unclear, and do not change any files.",
            "Проверь мои незакоммиченные изменения. Укажи ошибки, риски и всё неясное, файлы не меняй.",
          ),
        },
        {
          icon: "tests",
          title: t("Fix failing tests", "Починить упавшие тесты"),
          hint: t("Run them, find the cause, repair", "Запустить, найти причину, исправить"),
          prompt: t(
            "Run the tests and fix whatever fails. Tell me what the cause was.",
            "Запусти тесты и исправь всё, что падает. Скажи, в чём была причина.",
          ),
        },
      ],
    },
    {
      id: "documents",
      label: t("Documents", "Документы"),
      starters: [
        {
          icon: "collect",
          title: t("Collect data into a table", "Собрать данные в таблицу"),
          hint: t("Invoices, contracts or reports into one file", "Счета, договоры или отчёты в один файл"),
          prompt: t(
            "Read the documents in this folder and collect the key fields (date, counterparty, amount) into a CSV table. Next to every figure write the file it came from, and mark anything you could not read with certainty.",
            "Прочитай документы в этой папке и собери ключевые поля (дата, контрагент, сумма) в таблицу CSV. Рядом с каждой цифрой укажи файл, откуда она взята, а всё, что не удалось прочитать уверенно, пометь.",
          ),
        },
        {
          icon: "compare",
          title: t("Compare two documents", "Сравнить два документа"),
          hint: t("Every difference, quoted side by side", "Каждое отличие, с цитатами из обоих"),
          prompt: t(
            "I want to compare two documents. Ask me which ones, then list every difference and quote both versions.",
            "Я хочу сравнить два документа. Спроси меня, какие именно, затем перечисли все отличия и процитируй обе версии.",
          ),
        },
        {
          icon: "sort",
          title: t("Put a folder in order", "Навести порядок в папке"),
          hint: t("A plan first, nothing moves until you agree", "Сначала план, без вашего согласия ничего не двигается"),
          prompt: t(
            "Propose how to sort and rename the files in this folder by type and date. Show me the plan first and move nothing until I agree.",
            "Предложи, как отсортировать и переименовать файлы в этой папке по типу и дате. Сначала покажи план и ничего не переноси, пока я не соглашусь.",
          ),
        },
      ],
    },
    {
      id: "automation",
      label: t("Automation", "Автоматизация"),
      starters: [
        {
          icon: "routine",
          title: t("Automate a routine", "Автоматизировать рутину"),
          hint: t("Describe it once, get a script that does it", "Опишите один раз, получите скрипт"),
          prompt: t(
            "I want to automate a routine task. Ask me what I do by hand today, one question at a time, then write a script that does it, test it on a copy of the data and explain how to run it.",
            "Я хочу автоматизировать рутинную задачу. Спроси меня, что я делаю вручную сейчас, по одному вопросу за раз, затем напиши скрипт, проверь его на копии данных и объясни, как его запускать.",
          ),
        },
        {
          icon: "clean",
          title: t("Clean up a table", "Привести таблицу в порядок"),
          hint: t("Dates, duplicates, empty rows", "Даты, дубликаты, пустые строки"),
          prompt: t(
            "Clean up a table: unify the dates, remove duplicates and empty rows. Ask me which file, save the result as a new file and leave the original untouched.",
            "Приведи таблицу в порядок: единый формат дат, без дубликатов и пустых строк. Спроси меня, какой файл, сохрани результат в новый файл, оригинал не трогай.",
          ),
        },
        {
          icon: "fetch",
          title: t("Download data into a table", "Скачать данные в таблицу"),
          hint: t("From a web page or an API", "Со страницы сайта или из API"),
          prompt: t(
            "Write a script that downloads data from a web page or an API and saves it as a table. Ask me for the address and which data I need.",
            "Напиши скрипт, который скачивает данные со страницы сайта или из API и сохраняет их в таблицу. Спроси меня адрес и какие именно данные нужны.",
          ),
        },
      ],
    },
    {
      id: "build",
      label: t("Build", "Создать"),
      starters: [
        {
          icon: "site",
          title: t("A small website", "Небольшой сайт"),
          hint: t("From a description to working pages", "От описания до рабочих страниц"),
          prompt: t(
            "I want a simple website. Ask me what it is for and who will read it, one question at a time, then build it.",
            "Мне нужен простой сайт. Спроси меня, для чего он и кто его будет читать, по одному вопросу за раз, затем сделай его.",
          ),
        },
        {
          icon: "tool",
          title: t("A small tool", "Небольшая программа"),
          hint: t("One program for one job", "Одна программа для одной задачи"),
          prompt: t(
            "I want a small program for one job. Ask me what it should do, one question at a time, then build it and show me how to run it.",
            "Мне нужна небольшая программа для одной задачи. Спроси меня, что она должна делать, по одному вопросу за раз, затем сделай её и покажи, как запускать.",
          ),
        },
        {
          icon: "design",
          title: t("Recreate a design", "Повторить дизайн"),
          hint: t("Attach a screenshot, get a page", "Приложите скриншот, получите страницу"),
          prompt: t(
            "Recreate the attached design as a web page.",
            "Повтори приложенный дизайн в виде веб-страницы.",
          ),
        },
      ],
    },
  ]
}
