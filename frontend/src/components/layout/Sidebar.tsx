import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { MODULES } from '@/lib/modules'
import { cn } from '@/lib/cn'

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(true)

  return (
    <aside
      className={cn(
        'shrink-0 rounded-2xl border border-surface-border bg-surface-card shadow-sm flex flex-col transition-[width] duration-200 overflow-hidden',
        collapsed ? 'w-16' : 'w-56'
      )}
    >
      <div className={cn('h-14 flex items-center gap-2.5 border-b border-surface-border', collapsed ? 'justify-center px-0' : 'px-4')}>
        <div className="w-8 h-8 rounded-lg bg-brand-600 flex items-center justify-center shrink-0">
          <span className="text-white text-sm font-bold">TT</span>
        </div>
        {!collapsed && <span className="font-semibold text-foreground text-sm truncate">Workspace</span>}
      </div>

      <nav className="flex-1 py-3 px-2 space-y-0.5">
        {MODULES.map((m) => (
          <NavLink
            key={m.key}
            to={m.path}
            title={collapsed ? m.label : undefined}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-2.5 px-3 py-2 rounded-xl text-sm font-medium transition-colors',
                collapsed && 'justify-center px-0',
                isActive
                  ? 'bg-brand-50 text-brand-700 dark:bg-brand-600/15 dark:text-brand-400'
                  : 'text-muted hover:bg-surface hover:text-foreground'
              )
            }
          >
            <m.icon className="w-4 h-4 shrink-0" />
            {!collapsed && <span className="truncate">{m.label}</span>}
          </NavLink>
        ))}
      </nav>

      <div className="p-2 border-t border-surface-border">
        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          className={cn(
            'w-full flex items-center gap-2 px-3 py-2 rounded-xl text-sm font-medium text-muted hover:bg-surface hover:text-foreground transition-colors',
            collapsed && 'justify-center px-0'
          )}
        >
          {collapsed ? <PanelLeftOpen className="w-4 h-4 shrink-0" /> : <PanelLeftClose className="w-4 h-4 shrink-0" />}
          {!collapsed && <span>Collapse</span>}
        </button>
      </div>
    </aside>
  )
}
