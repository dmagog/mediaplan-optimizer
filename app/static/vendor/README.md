# Сторонние материалы кабинета

Лежат в репозитории, а не тянутся из сети: демо должно открываться без
интернета, а версия — не меняться между показами.

| Что | Версия | Откуда | Лицензия |
|---|---|---|---|
| `chart.umd.min.js` | Chart.js 4.4.1 | https://github.com/chartjs/Chart.js | MIT, текст в [LICENSE-chartjs.md](LICENSE-chartjs.md) |
| `fonts/archivo-*.woff2` | Archivo | https://fonts.google.com/specimen/Archivo | SIL OFL 1.1, текст в [fonts/OFL-archivo.txt](fonts/OFL-archivo.txt) |
| `fonts/golos-*.woff2` | Golos Text | https://fonts.google.com/specimen/Golos+Text | SIL OFL 1.1, текст в [fonts/OFL-golos-text.txt](fonts/OFL-golos-text.txt) |

| иконка шестерёнки в шапке | Bootstrap Icons | https://icons.getbootstrap.com | MIT, текст в [LICENSE-bootstrap-icons.txt](LICENSE-bootstrap-icons.txt) |

Иконка вшита разметкой в `../index.html`, файла в каталоге нет: контур `gear`
совпадает с оригиналом байт в байт.

Archivo — латиница и цифры, кириллицы в нём нет, поэтому русский текст набирается
Golos Text. Подключение — [fonts/fonts.css](fonts/fonts.css).

Контуры федеральных округов в `../russia.svg` — данные Natural Earth (public
domain). Население округов — сводка данных Росстата, дата снимка приходит в
`/api/meta` и подписана в подвале кабинета.
