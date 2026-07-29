// Next local-tz occurrence of a recurring weekly window. day_of_week is
// 0=Sunday … 6=Saturday — the same convention as JS Date.getDay() (mirrors
// upload_intel _DAY_NAMES). If the window is today but the hour has passed,
// roll a full week forward: the backend 422s non-future datetimes.
export function nextOccurrence(dayOfWeek: number, hour: number, from = new Date()): Date {
  const d = new Date(from)
  d.setHours(hour, 0, 0, 0)
  let delta = (dayOfWeek - d.getDay() + 7) % 7
  if (delta === 0 && d <= from) delta = 7
  d.setDate(d.getDate() + delta)
  return d
}
