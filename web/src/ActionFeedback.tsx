import { useEffect, useRef, useState } from 'react'

export type ReportProblem = (message: string) => void
export function invalidFields(checks: [unknown, string][], report?: ReportProblem): boolean {
  const invalid = checks.filter(([condition]) => Boolean(condition))
  invalid.forEach(([, message]) => report?.(message))
  return invalid.length > 0
}

// The caller keeps its existing submission gates. Feedback never sends/retries
// requests or changes a draft; current problems disappear as the user fixes them.
export function useActionFeedback(problems: string[]) {
  const [attempt, setAttempt] = useState(0)
  const target = useRef<HTMLDivElement>(null)
  useEffect(() => { if (attempt) target.current?.focus() }, [attempt])
  return {
    check: () => {
      if (!problems.length) { setAttempt(0); return true }
      setAttempt(old => old + 1); return false
    },
    clear: () => setAttempt(0),
    feedback: attempt > 0 && problems.length > 0 ? <div ref={target} role="alert" tabIndex={-1} className="error" aria-label="Action needs attention">
      <strong>Check before continuing</strong><ul>{[...new Set(problems)].map(message => <li key={message}>{message}</li>)}</ul>
      <p>Your draft is preserved. No request was sent.</p>
    </div> : null,
  }
}
