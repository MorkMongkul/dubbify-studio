// src/components/layout/AppSidebar.tsx
import { NavLink } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { LayoutGrid, Settings2, Zap, Sun, Moon, Film, Mic } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Tooltip } from '@/components/ui/Tooltip'
import { useThemeStore } from '@/store/themeStore'

interface AppSidebarProps {
  collapsed: boolean
  onToggle: () => void
}

const navItems = [
  { to: '/projects', icon: LayoutGrid, label: 'Projects' },
  { to: '/voices',   icon: Mic,        label: 'Voices'   },
  { to: '/settings', icon: Settings2,  label: 'Settings'  },
]

export function AppSidebar({ collapsed, onToggle }: AppSidebarProps) {
  const { theme, toggleTheme } = useThemeStore()

  return (
    <motion.aside
      className="relative flex flex-col h-full z-10 border-r overflow-hidden shrink-0"
      style={{ background: 'var(--color-surface-1)', borderColor: 'var(--color-border)' }}
      animate={{ width: collapsed ? 48 : 200 }}
      transition={{ type: 'tween', duration: 0.14, ease: 'easeInOut' }}
    >
      {/* Logo / Brand — click to toggle sidebar */}
      <Tooltip content={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} side="right">
        <div
          className="flex items-center gap-2.5 px-3 h-10 border-b cursor-pointer shrink-0 overflow-hidden select-none group"
          style={{ borderColor: 'var(--color-border)' }}
          onClick={onToggle}
        >
          <div className="shrink-0 h-6 w-6 rounded bg-brand flex items-center justify-center transition-transform duration-100 group-hover:scale-105">
            <Zap size={13} className="text-white" fill="white" />
          </div>
          <AnimatePresence initial={false}>
            {!collapsed && (
              <motion.span
                initial={{ opacity: 0, x: -4 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -4 }}
                transition={{ duration: 0.1 }}
                className="font-bold text-[13px] whitespace-nowrap tracking-tight text-white"
              >
                Dubify<span className="text-brand-400"> Studio</span>
              </motion.span>
            )}
          </AnimatePresence>
        </div>
      </Tooltip>

      {/* Nav items */}
      <div className="flex-1 flex flex-col gap-0.5 px-1.5 py-2 overflow-hidden">
        {navItems.map(({ to, icon: Icon, label }) => {
          const item = (
            <NavLink
              key={to}
              to={to}
              end
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-2 rounded-md px-2 py-1.5 text-[13px] font-medium transition-colors duration-100 group overflow-hidden',
                  isActive
                    ? 'bg-brand/15 text-white'
                    : 'text-white/45 hover:text-white hover:bg-white/6',
                  collapsed && 'justify-center px-0 w-8 mx-auto'
                )
              }
            >
              {({ isActive }) => (
                <>
                  <Icon
                    size={15}
                    className={cn(
                      'shrink-0',
                      isActive ? 'text-brand-400' : 'text-white/45 group-hover:text-white/80'
                    )}
                  />
                  <AnimatePresence initial={false}>
                    {!collapsed && (
                      <motion.span
                        initial={{ opacity: 0, x: -4 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: -4 }}
                        transition={{ duration: 0.1 }}
                        className="whitespace-nowrap"
                      >
                        {label}
                      </motion.span>
                    )}
                  </AnimatePresence>
                </>
              )}
            </NavLink>
          )
          return collapsed
            ? <Tooltip key={to} content={label} side="right">{item}</Tooltip>
            : item
        })}
      </div>

      {/* Bottom — version + theme toggle */}
      <div
        className="px-1.5 py-2 border-t flex items-center gap-1 overflow-hidden"
        style={{ borderColor: 'var(--color-border)' }}
      >
        {/* Theme toggle */}
        <Tooltip content={theme === 'dark' ? 'Light mode' : 'Dark mode'} side="right">
          <button
            onClick={toggleTheme}
            className={cn(
              'flex items-center justify-center rounded-md transition-colors duration-100 shrink-0',
              'text-white/40 hover:text-white hover:bg-white/8',
              collapsed ? 'h-8 w-8 mx-auto' : 'h-7 w-7'
            )}
          >
            {theme === 'dark'
              ? <Sun size={13} />
              : <Moon size={13} />
            }
          </button>
        </Tooltip>

        <AnimatePresence initial={false}>
          {!collapsed && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.1 }}
              className="flex items-center gap-1.5 min-w-0"
            >
              <Film size={10} className="text-white/20 shrink-0" />
              <span className="text-[10px] text-white/20 truncate">v0.1.0</span>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

    </motion.aside>
  )
}
