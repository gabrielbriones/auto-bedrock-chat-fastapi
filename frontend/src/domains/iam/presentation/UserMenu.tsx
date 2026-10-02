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

export type UserMenuProps = {
  /** `rail` is the collapsed sidebar: avatar only, but still at the foot of the roster. */
  readonly variant?: 'sidebar' | 'rail' | 'header'
}

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

function LogoutItem() {
  const { identity, logger } = useContainer()

  return (
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
  )
}

// FR-IAM-016: absent when unauthenticated or when authentication is switched off.
export function UserMenu({ variant = 'sidebar' }: UserMenuProps) {
  const { authPolicy } = useContainer()
  const session = useContainerStore('identity')

  if (!authPolicy.enabled || session.status !== 'authenticated') {
    return null
  }

  const { displayName } = session.principal
  const iconOnly = variant !== 'sidebar'

  const wrapperClassName = variant === 'sidebar'
    ? 'border-t border-border p-2'
    : variant === 'rail'
      ? 'flex justify-center border-t border-border p-2'
      : undefined

  return (
    <div className={wrapperClassName}>
      <DropdownMenu>
        <DropdownMenuTrigger
          render={
            <Button
              type="button"
              variant="ghost"
              size={iconOnly ? 'icon' : 'default'}
              aria-label={displayName ?? IAM_COPY.status.account}
              className={iconOnly ? 'size-8 p-0' : 'w-full min-w-0 justify-start gap-2 px-2'}
            />
          }
        >
          <Avatar size="sm">
            <AvatarFallback>{displayName !== null ? initialsOf(displayName) : '?'}</AvatarFallback>
          </Avatar>
          {!iconOnly && displayName !== null ? (
            <span className="min-w-0 flex-1 truncate text-start text-sm font-medium">
              {displayName}
            </span>
          ) : null}
        </DropdownMenuTrigger>

        <DropdownMenuContent align={variant === 'header' ? 'end' : 'start'}>
          <DropdownMenuLabel>{displayName ?? IAM_COPY.status.account}</DropdownMenuLabel>
          <ThemeOptions />
          <LogoutItem />
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}
