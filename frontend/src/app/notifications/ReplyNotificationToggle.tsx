import { useCallback } from 'react'
import { BellIcon, BellOffIcon } from 'lucide-react'

import { useContainer, useContainerStore } from '@/app/bootstrap/container-context'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { REPLY_NOTIFICATION_COPY as COPY } from '@/shared/copy/reply-notifications'

// FR-MSG-032. Absent when the browser has no Notifications API, so an unsupported browser sees
// the header exactly as before. Switching on is the user gesture that may ask for permission.
export function ReplyNotificationToggle() {
  const { replyNotifications, notifications } = useContainer()
  const { supported, active } = useContainerStore('replyNotifications')

  const toggle = useCallback(() => {
    if (active) {
      replyNotifications.disable()
      return
    }

    void replyNotifications.enable().then((permission) => {
      if (permission === 'granted') {
        notifications.success(COPY.enabled.title, { description: COPY.enabled.description })
      } else if (permission === 'denied') {
        notifications.warning(COPY.blocked.title, { description: COPY.blocked.description })
      }
    })
  }, [active, notifications, replyNotifications])

  if (!supported) {
    return null
  }

  const label = active ? COPY.toggle.disable : COPY.toggle.enable

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button type="button" variant="ghost" size="icon" aria-label={label} aria-pressed={active} onClick={toggle} />
        }
      >
        {active ? <BellIcon aria-hidden /> : <BellOffIcon aria-hidden />}
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}
