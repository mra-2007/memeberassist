import { FlaskConical } from 'lucide-react'

/**
 * Always-visible reminder that every member, claim and amount in this
 * deployment is synthetic. Required by the project's honesty rules.
 */
export function SyntheticDataBadge() {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-warn-500/10 px-2.5 py-1 text-xs font-medium text-warn-500 ring-1 ring-warn-500/25 ring-inset">
      <FlaskConical aria-hidden="true" className="size-3.5" />
      Synthetic demo data
    </span>
  )
}
