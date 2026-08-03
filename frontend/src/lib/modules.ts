/**
 * modules.ts
 *
 * Sidebar module registry. Each entry maps to a permission module in
 * the backend's ALL_MODULES (app/auth/service.py) — adding a new module
 * here + there + a route is the whole "register a new module" flow.
 */

import type { LucideIcon } from 'lucide-react'
import { Languages } from 'lucide-react'

export interface ModuleDef {
  key: string
  label: string
  path: string
  icon: LucideIcon
}

export const MODULES: ModuleDef[] = [
  { key: 'translate', label: 'Thought Translate', path: '/translate', icon: Languages },
]
