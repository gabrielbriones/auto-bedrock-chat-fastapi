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
      <DropdownMenuLabel>{IAM_COPY.status.theme}</DropdownMenuLabel>
      <DropdownMenuRadioGroup
        value={preference}
        onValueChange={(value: unknown) => {
          if (value === 'light' || value === 'dark' || value === 'system') setPreference(value)
        }}
      >
        <DropdownMenuRadioItem value="light"><SunIcon aria-hidden />{IAM_COPY.status.light}</DropdownMenuRadioItem>
        <DropdownMenuRadioItem value="dark"><MoonIcon aria-hidden />{IAM_COPY.status.dark}</DropdownMenuRadioItem>
        <DropdownMenuRadioItem value="system"><MonitorIcon aria-hidden />{IAM_COPY.status.system}</DropdownMenuRadioItem>
      </DropdownMenuRadioGroup>
    </DropdownMenuGroup>
  )
}

// FR-IAM-016: the authenticated identity now sits at the foot of the conversation roster rather
// than the header, next to the logout action it owns. Absent when unauthenticated or when the
// deployment has authentication switched off, since there is no identity to show or discard.
export function UserMenu() {
  const { identity, authPolicy, logger } = useContainer()
  const session = useContainerStore('identity')

  if (!authPolicy.enabled || session.status !== 'authenticated') {
    return null
  }

  const { displayName } = session.principal

  return (
    <div className="border-t border-border p-2">
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              type="button"
              variant="ghost"
              aria-label={displayName ?? IAM_COPY.status.account}
              className="w-full min-w-0 justify-start gap-2 px-2"
            />
          }
        >
          <Avatar size="sm">
            <AvatarFallback>{displayName !== null ? initialsOf(displayName) : '?'}</AvatarFallback>
          </Avatar>
          {displayName !== null ? (
            <span className="min-w-0 flex-1 truncate text-start text-sm font-medium">
              {displayName}
            </span>
          ) : null}
        </DropdownMenuTrigger>

        <DropdownMenuContent align="start">
          <ThemeOptions />
          <DropdownMenuItem
            variant="destructive"
            onClick={() => {
              identity.logout().catch((error: unknown) => {
                logger.error('logout_failed', { name: String(error) })
              })
            }}
          >
            <LogOutIcon aria-hidden />
            {IAM_COPY.status.logOut}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}
