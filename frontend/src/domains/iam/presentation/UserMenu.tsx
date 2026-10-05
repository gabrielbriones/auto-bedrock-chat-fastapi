import { LogOutIcon, MonitorIcon, MoonIcon, SunIcon } from 'lucide-react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { useTheme } from '@/app/providers/theme-context'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { IAM_COPY } from '@/shared/copy/iam'

const initialsOf = (displayName: string): string => {
  const [first, second] = displayName.trim().split(/\s+/)
  return `${first?.charAt(0) ?? ''}${second?.charAt(0) ?? ''}`.toUpperCase() || '?'
}

function ThemeOptions() {
  const { preference, setPreference } = useTheme()

  return (
    <DropdownMenuGroup>
      <DropdownMenuLabel className="px-2">{IAM_COPY.status.theme}</DropdownMenuLabel>
      <DropdownMenuRadioGroup
        value={preference}
        onValueChange={(value: unknown) => {
          if (value === 'light' || value === 'dark' || value === 'system') setPreference(value)
        }}
      >
        <DropdownMenuRadioItem value="light" className="py-1.5 ps-2"><SunIcon aria-hidden />{IAM_COPY.status.light}</DropdownMenuRadioItem>
        <DropdownMenuRadioItem value="dark" className="py-1.5 ps-2"><MoonIcon aria-hidden />{IAM_COPY.status.dark}</DropdownMenuRadioItem>
        <DropdownMenuRadioItem value="system" className="py-1.5 ps-2"><MonitorIcon aria-hidden />{IAM_COPY.status.system}</DropdownMenuRadioItem>
      </DropdownMenuRadioGroup>
    </DropdownMenuGroup>
  )
}

function LogoutItem() {
  const { identity, logger } = useContainer()

  return (
    <DropdownMenuItem
      variant="destructive"
      className="px-2 py-1.5"
      onClick={() => {
        identity.logout().catch((error: unknown) => {
          logger.error('logout_failed', { name: String(error) })
        })
      }}
    >
      <LogOutIcon aria-hidden />
      {IAM_COPY.status.logOut}
    </DropdownMenuItem>
  )
}

// FR-IAM-016: absent when unauthenticated or when authentication is switched off.
export function UserMenu() {
  const { authPolicy } = useContainer()
  const session = useContainerStore('identity')

  if (!authPolicy.enabled || session.status !== 'authenticated') {
    return null
  }

  const { displayName } = session.principal

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            size="icon"
            aria-label={displayName ?? IAM_COPY.status.account}
            className="group/user size-10 rounded-full p-0 hover:bg-transparent aria-expanded:bg-transparent"
          />
        }
      >
        {/* The avatar covers the whole trigger, so the ghost button's own hover background never shows. */}
        <Avatar
          size="lg"
          className="transition-shadow group-hover/user:ring-2 group-hover/user:ring-ring/50 group-aria-expanded/user:ring-2 group-aria-expanded/user:ring-ring/50"
        >
          <AvatarFallback className="transition-colors group-hover/user:bg-accent group-hover/user:text-accent-foreground">
            {displayName !== null ? initialsOf(displayName) : '?'}
          </AvatarFallback>
        </Avatar>
      </DropdownMenuTrigger>

      {/* The 40px trigger sits 8px above the 56px header's bottom border; clear it. */}
      <DropdownMenuContent align="end" sideOffset={14} className="w-auto min-w-60 p-1.5">
        <DropdownMenuGroup>
          <DropdownMenuLabel className="truncate px-2 py-2 text-sm font-medium text-foreground">
            {displayName ?? IAM_COPY.status.account}
          </DropdownMenuLabel>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <ThemeOptions />
        <DropdownMenuSeparator />
        <LogoutItem />
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
