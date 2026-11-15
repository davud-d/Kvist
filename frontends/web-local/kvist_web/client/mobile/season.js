// Winter runs from 1 December to the end of February. ?winter=1 shows it in any month, so it can be
// checked before the season; ?winter=0 turns it off in the season.
const DECEMBER = 11;
const JANUARY = 0;
const FEBRUARY = 1;

export function isWinter(now = new Date(), search = '') {
    const flag = new URLSearchParams(search).get('winter');
    if (flag === '1') return true;
    if (flag === '0') return false;
    const month = now.getMonth();
    return month === DECEMBER || month === JANUARY || month === FEBRUARY;
}
