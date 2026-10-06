import { Switch } from '@/components/ui/switch'
import { Tip } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { Loader2 } from '@/lib/icons'

interface CatalogInstallSwitchProps {
  name: string
  installed: boolean
  installing: boolean
  disabled: boolean
  /** Why this row cannot install (e.g. a different skill already holds the name).
   *  Rendered as the tooltip so the disabled switch explains itself. */
  blockedReason?: string
  onInstall: () => void
}

/** Turning it on installs. Installed entries normally show their owner's on/off
 *  switch instead; this one only stays checked (and disabled) as a fallback. */
export function CatalogInstallSwitch({ name, installed, installing, disabled, blockedReason, onInstall }: CatalogInstallSwitchProps) {
  const { t } = useI18n()
  const label = blockedReason ?? `${installed ? t.catalog.added : t.catalog.add} ${name}`

  return (
    <Tip label={label}>
      <span className="inline-flex items-center gap-2">
        {installing && <Loader2 className="size-3 animate-spin text-(--ui-text-tertiary)" />}
        <Switch
          aria-busy={installing}
          aria-label={label}
          checked={installed}
          disabled={disabled || installing}
          onCheckedChange={checked => checked && onInstall()}
          size="xs"
        />
      </span>
    </Tip>
  )
}
