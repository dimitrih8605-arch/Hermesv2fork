import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { cn } from '@/lib/utils'

interface Email {
  id: string
  subject: string
  from: string
  date: string
  snippet: string
}

const API = 'http://127.0.0.1:19876/api/email'

export function EmailView() {
  const [emails, setEmails] = useState<Email[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await fetch(`${API}/inbox`)
      const data = await r.json()
      setEmails(Array.isArray(data) ? data : [])
    } catch {
      setEmails([])
    }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b px-4 py-2">
        <h2 className="text-sm font-semibold">Inbox</h2>
        <Button variant="ghost" size="icon" onClick={load} disabled={loading}>
          <Codicon name={loading ? 'sync~spin' : 'refresh'} />
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto">
        {loading && emails.length === 0 && (
          <div className="flex items-center justify-center py-8 text-xs text-muted-foreground">Loading...</div>
        )}
        {!loading && emails.length === 0 && (
          <div className="flex items-center justify-center py-8 text-xs text-muted-foreground">No emails</div>
        )}
        {emails.map(m => (
          <button
            key={m.id}
            onClick={() => setSelected(selected === m.id ? null : m.id)}
            className={cn(
              'w-full border-b px-4 py-2.5 text-left transition-colors hover:bg-accent/50',
              selected === m.id && 'bg-accent'
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-xs font-medium">{m.subject || '(no subject)'}</span>
              <span className="shrink-0 text-[10px] text-muted-foreground">
                {m.date ? m.date.slice(0, 16) : ''}
              </span>
            </div>
            <div className="mt-0.5 truncate text-[11px] text-muted-foreground">{m.from}</div>
            {selected === m.id && m.snippet && (
              <div className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">{m.snippet}</div>
            )}
          </button>
        ))}
      </div>
    </div>
  )
}
