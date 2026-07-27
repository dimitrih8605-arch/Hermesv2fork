import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { ErrorBanner } from '@/components/ui/error-state'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { pluginRest, pluginSocket } from '@/hermes'
import { MoreHorizontal, Plus, RefreshCw, Save, Trash2, X } from '@/lib/icons'
import { queryClient } from '@/lib/query-client'
import { notifyError } from '@/store/notifications'

import { useRefreshHotkey } from '../hooks/use-refresh-hotkey'
import type { SetStatusbarItemGroup } from '../shell/statusbar-controls'

// ── Types ────────────────────────────────────────────────────────────────

interface KanbanTask {
  id: string
  title: string
  status: string
  priority?: number
  assignee?: string | null
  body?: string | null
  tags?: string | null
  due_date?: string | null
  created_at?: number
}

interface ColumnGroup {
  name: string
  tasks: KanbanTask[]
}

interface KanbanBoardResponse {
  columns: ColumnGroup[]
  latest_event_id: number
}

const COLUMN_LABELS: Record<string, string> = {
  triage: 'Triage', todo: 'Todo', scheduled: 'Scheduled', ready: 'Ready',
  running: 'Running', blocked: 'Blocked', review: 'Review', done: 'Done'
}

const STATUS_COLORS: Record<string, string> = {
  triage: 'bg-amber-500', todo: 'bg-sky-500', scheduled: 'bg-violet-500',
  ready: 'bg-emerald-500', running: 'bg-blue-500', blocked: 'bg-red-500',
  review: 'bg-orange-500', done: 'bg-neutral-500'
}

const PRIORITY_LABELS = ['Low', 'Medium', 'High', 'Urgent']
const QUERY_KEY = ['kanban-board']

// ── Component ────────────────────────────────────────────────────────────

export function KanbanView({ setStatusbarItemGroup }: { setStatusbarItemGroup?: SetStatusbarItemGroup }) {
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [newAssignee, setNewAssignee] = useState('')
  const [newPriority, setNewPriority] = useState('0')
  const [newTags, setNewTags] = useState('')
  const [newDueDate, setNewDueDate] = useState('')
  const [editTask, setEditTask] = useState<KanbanTask | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editAssignee, setEditAssignee] = useState('')
  const [editPriority, setEditPriority] = useState('0')
  const [editBody, setEditBody] = useState('')
  const [editTags, setEditTags] = useState('')
  const [editDueDate, setEditDueDate] = useState('')
  const [draggedId, setDraggedId] = useState<string | null>(null)

  // ── Query ──────────────────────────────────────────────────────────────
  const { data, isPending, refetch } = useQuery({
    queryKey: QUERY_KEY,
    queryFn: async () => {
      setError(null)

      try { return await pluginRest<KanbanBoardResponse>('kanban', '/board') }
      catch (e) { const msg = e instanceof Error ? e.message : 'Failed'; setError(msg); throw e }
    }
  })

  // ponytail: pluginSocket for live updates, polling fallback if WS dies
  useEffect(() => {
    let active = true
    let dispose: (() => void) | null = null
    let pollId: ReturnType<typeof setInterval> | undefined

    try {
      dispose = pluginSocket('kanban', '/events', () => {
        if (active) {queryClient.invalidateQueries({ queryKey: QUERY_KEY })}
      })
    } catch {
      pollId = setInterval(() => {
        if (active) {queryClient.invalidateQueries({ queryKey: QUERY_KEY })}
      }, 30_000)
    }

    return () => { active = false; dispose?.(); clearInterval(pollId) }
  }, [])

  // ── Mutations ──────────────────────────────────────────────────────────
  const invalidate = () => queryClient.invalidateQueries({ queryKey: QUERY_KEY })

  const createTask = useMutation({
    mutationFn: (body: { title: string; assignee?: string; priority?: number; tags?: string; due_date?: string }) =>
      pluginRest<{ task?: KanbanTask }>('kanban', '/tasks', { method: 'POST', body: { ...body, triage: true } }),
    onSuccess: () => { setNewTitle(''); setNewAssignee(''); setNewPriority('0'); setNewTags(''); setNewDueDate(''); setAdding(false); invalidate() },
    onError: (e: Error) => notifyError(e.message, 'Failed to create task')
  })

  const updateTask = useMutation({
    mutationFn: ({ id, ...body }: { id: string; title?: string; status?: string; assignee?: string; priority?: number; body?: string; tags?: string; due_date?: string }) =>
      pluginRest('kanban', `/tasks/${id}`, { method: 'PATCH', body }),
    onSuccess: () => { setEditTask(null); invalidate() },
    onError: (e: Error) => notifyError(e.message, 'Failed to update task')
  })

  const deleteTask = useMutation({
    mutationFn: (id: string) => pluginRest('kanban', `/tasks/${id}`, { method: 'DELETE' }),
    onSuccess: () => { setEditTask(null); invalidate() },
    onError: (e: Error) => notifyError(e.message, 'Failed to delete task')
  })

  const moveTask = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      pluginRest('kanban', `/tasks/${id}`, { method: 'PATCH', body: { status } }),
    onError: (e: Error) => notifyError(e.message, 'Failed to move task'),
    onSettled: () => { setDraggedId(null); invalidate() }
  })

  useRefreshHotkey(refetch)

  const columns = data?.columns ?? []

  // ── Drag helpers ───────────────────────────────────────────────────────
  const onDragStart = (taskId: string) => (e: React.DragEvent) => {
    setDraggedId(taskId)
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', taskId)
  }

  const onDragOver = (e: React.DragEvent) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move' }

  const onDrop = (status: string) => (e: React.DragEvent) => {
    e.preventDefault()
    const id = e.dataTransfer.getData('text/plain')

    if (id && draggedId) {moveTask.mutate({ id, status })}
  }

  // ── Edit helpers ───────────────────────────────────────────────────────
  const openEdit = (task: KanbanTask) => {
    setEditTask(task)
    setEditTitle(task.title)
    setEditAssignee(task.assignee ?? '')
    setEditPriority(String(task.priority ?? 0))
    setEditBody(task.body ?? '')
    setEditTags(task.tags ?? '')
    setEditDueDate(task.due_date ?? '')
  }

  const saveEdit = () => {
    if (!editTask) {return}
    updateTask.mutate({
      id: editTask.id,
      title: editTitle.trim() || undefined,
      assignee: editAssignee.trim() || undefined,
      priority: parseInt(editPriority, 10) || 0,
      body: editBody.trim() || undefined,
      tags: editTags.trim() || undefined,
      due_date: editDueDate.trim() || undefined
    })
  }

  const handleAdd = () => {
    const title = newTitle.trim()

    if (!title) {return}
    createTask.mutate({
      title,
      assignee: newAssignee.trim() || undefined,
      priority: parseInt(newPriority, 10) || 0,
      tags: newTags.trim() || undefined,
      due_date: newDueDate.trim() || undefined
    })
  }

  // ── Render helpers ─────────────────────────────────────────────────────
  const tagEl = (t: string) => (
    <span className="inline-block rounded-sm bg-(--ui-control-hover-background) px-1.5 py-px text-[9px] font-medium text-(--ui-text-secondary)" key={t}>{t}</span>
  )

  const dueBadge = (d: string) => {
    const remaining = Math.ceil((new Date(d).getTime() - Date.now()) / 86400000)
    const overdue = remaining < 0
    const soon = remaining >= 0 && remaining <= 2

    return (
      <span className={`inline-block rounded-sm px-1.5 py-px text-[9px] font-medium ${
        overdue ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' :
        soon ? 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400' :
        'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
      }`}>
        {overdue ? 'Overdue' : soon ? `${remaining}d left` : d}
      </span>
    )
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* Header */}
      <header className="flex shrink-0 items-center gap-3 border-b px-6 py-3">
        <h1 className="text-lg font-semibold tracking-tight">Kanban</h1>
        <Button disabled={isPending} onClick={() => refetch()} size="icon-sm" variant="ghost">
          <RefreshCw className={isPending ? 'animate-spin' : ''} />
        </Button>
      </header>

      {error && !isPending && (
        <div className="shrink-0 px-6 pt-3">
          <ErrorBanner><span>{error}</span></ErrorBanner>
        </div>
      )}

      {isPending && <div className="flex flex-1 items-center justify-center"><PageLoader /></div>}

      {!isPending && (
        <div className="flex flex-1 gap-3 overflow-x-auto p-4">
          {/* Add column */}
          <div
            className={`flex shrink-0 flex-col rounded-lg border border-dashed border-(--ui-border-muted) bg-(--ui-editor-surface-background) transition-all ${adding ? 'w-64' : 'w-12 cursor-pointer hover:border-(--ui-border-hover)'}`}
            onClick={() => { if (!adding) {setAdding(true)} }}
          >
            {adding ? (
              <div className="flex flex-col gap-2 p-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold tracking-wide text-(--ui-text-tertiary) uppercase">New Task</span>
                  <button className="text-(--ui-text-tertiary) hover:text-foreground" onClick={() => setAdding(false)}><X className="h-4 w-4" /></button>
                </div>
                <Input autoFocus onChange={e => setNewTitle(e.target.value)} placeholder="Title" value={newTitle} />
                <Input onChange={e => setNewAssignee(e.target.value)} placeholder="Assignee" value={newAssignee} />
                <Select onValueChange={setNewPriority} value={newPriority}>
                  <SelectTrigger className="h-8 text-xs"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {PRIORITY_LABELS.map((l, i) => (
                      <SelectItem key={i} value={String(i)}>{l}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Input onChange={e => setNewTags(e.target.value)} placeholder="Tags (comma-sep)" value={newTags} />
                <Input className="h-8" onChange={e => setNewDueDate(e.target.value)} type="date" value={newDueDate} />
                <Button disabled={!newTitle.trim() || createTask.isPending} onClick={handleAdd} size="sm">
                  {createTask.isPending ? 'Adding…' : 'Add'}
                </Button>
              </div>
            ) : (
              <div className="flex h-full items-center justify-center"><Plus className="h-5 w-5 text-(--ui-text-tertiary)" /></div>
            )}
          </div>

          {/* Board columns */}
          {columns.map(col => (
            <div
              className={`flex w-56 shrink-0 flex-col rounded-lg border transition-shadow ${draggedId ? 'border-dashed' : ''} bg-(--ui-editor-surface-background)`}
              key={col.name}
              onDragOver={onDragOver}
              onDrop={onDrop(col.name)}
            >
              <div className="flex items-center gap-2 border-b px-3 py-2">
                <span className={`h-2 w-2 shrink-0 rounded-full ${STATUS_COLORS[col.name] ?? 'bg-neutral-400'}`} />
                <span className="text-sm font-medium">{COLUMN_LABELS[col.name] ?? col.name}</span>
                <Badge className="ml-auto text-[10px]" variant="outline">{col.tasks.length}</Badge>
              </div>

              <div className="flex flex-col gap-1.5 overflow-y-auto p-2 min-h-24">
                {col.tasks.length === 0 && (
                  <p className="py-4 text-center text-xs text-(--ui-text-tertiary)">Empty</p>
                )}
                {col.tasks.map(task => (
                  <div
                    className={`cursor-grab active:cursor-grabbing rounded-md border bg-(--ui-surface-background) p-2 text-xs leading-relaxed hover:border-(--ui-border-hover) transition-opacity ${draggedId === task.id ? 'opacity-40' : ''}`}
                    draggable
                    key={task.id}
                    onClick={() => openEdit(task)}
                    onDragStart={onDragStart(task.id)}
                  >
                    <div className="flex items-start gap-1.5">
                      <span className="mt-0.5 shrink-0 text-(--ui-text-muted) cursor-grab"><MoreHorizontal className="h-3 w-3" /></span>
                      <div className="min-w-0 flex-1">
                        <p className="line-clamp-3 font-medium">{task.title}</p>

                        {/* Tags */}
                        {task.tags && (
                          <div className="mt-1 flex flex-wrap gap-1">
                            {task.tags.split(',').map(t => t.trim()).filter(Boolean).map(tagEl)}
                          </div>
                        )}

                        <div className="mt-1 flex flex-wrap items-center gap-1.5">
                          {task.assignee && <span className="text-(--ui-text-tertiary)">@{task.assignee}</span>}
                          {(task.priority ?? 0) > 0 && (
                            <span className={`rounded-sm px-1 py-px text-[9px] font-medium ${
                              (task.priority ?? 0) >= 3 ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' :
                              (task.priority ?? 0) >= 2 ? 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400' :
                              'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
                            }`}>
                              {PRIORITY_LABELS[task.priority ?? 0]}
                            </span>
                          )}
                          {task.due_date && dueBadge(task.due_date)}
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Edit dialog */}
      {editTask && (
        <Dialog onOpenChange={o => { if (!o) {setEditTask(null)} }} open>
          <DialogContent className="sm:max-w-md">
            <DialogHeader><DialogTitle>Edit Task</DialogTitle></DialogHeader>
            <div className="flex flex-col gap-3 px-6 py-4">
              <label className="text-xs font-medium text-(--ui-text-tertiary)">Title</label>
              <Input onChange={e => setEditTitle(e.target.value)} value={editTitle} />

              <label className="text-xs font-medium text-(--ui-text-tertiary)">Assignee</label>
              <Input onChange={e => setEditAssignee(e.target.value)} placeholder="Unassigned" value={editAssignee} />

              <label className="text-xs font-medium text-(--ui-text-tertiary)">Priority</label>
              <Select onValueChange={setEditPriority} value={editPriority}>
                <SelectTrigger className="h-8"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {PRIORITY_LABELS.map((l, i) => (
                    <SelectItem key={i} value={String(i)}>{l}</SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <label className="text-xs font-medium text-(--ui-text-tertiary)">Tags</label>
              <Input onChange={e => setEditTags(e.target.value)} placeholder="comma-separated" value={editTags} />

              <label className="text-xs font-medium text-(--ui-text-tertiary)">Due Date</label>
              <Input onChange={e => setEditDueDate(e.target.value)} type="date" value={editDueDate} />

              <label className="text-xs font-medium text-(--ui-text-tertiary)">Description</label>
              <Textarea onChange={e => setEditBody(e.target.value)} rows={4} value={editBody} />
              <p className="text-[10px] text-(--ui-text-tertiary)">Status: <span className="font-medium">{COLUMN_LABELS[editTask.status] ?? editTask.status}</span></p>
            </div>
            <DialogFooter className="flex items-center justify-between">
              <Button disabled={deleteTask.isPending} onClick={() => deleteTask.mutate(editTask.id)} size="sm" variant="destructive">
                <Trash2 className="mr-1 h-3 w-3" /> Delete
              </Button>
              <div className="flex gap-2">
                <DialogClose asChild><Button size="sm" variant="ghost">Cancel</Button></DialogClose>
                <Button disabled={updateTask.isPending} onClick={saveEdit} size="sm">
                  <Save className="mr-1 h-3 w-3" /> Save
                </Button>
              </div>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </div>
  )
}
