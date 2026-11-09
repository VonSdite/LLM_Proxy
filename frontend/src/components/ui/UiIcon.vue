<script setup lang="ts">
import {
  CircleHelp,
  AlertCircle,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronsUpDown,
  X,
  Plus,
  Trash2,
  Pencil,
  Copy,
  Download,
  Upload,
  FileInput,
  FileOutput,
  RefreshCw,
  Search,
  Settings2,
  Sun,
  Moon,
  LogOut,
  Server,
  Monitor,
  FileCode2,
  KeyRound,
  Link,
  GripVertical,
  ArrowUp,
  ArrowDown,
  MoreHorizontal,
  Eye,
  EyeOff,
  LoaderCircle,
  SlidersHorizontal,
  ExternalLink,
  Shield,
  Users,
  ChartNoAxesCombined,
  Network,
  Terminal,
  Save,
  FolderOpen,
  Plug,
  Unplug,
  RotateCcw,
  CheckCheck,
  Ban,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
} from '@lucide/vue'
const icons = {
  help: CircleHelp,
  alert: AlertCircle,
  check: Check,
  down: ChevronDown,
  left: ChevronLeft,
  right: ChevronRight,
  sort: ChevronsUpDown,
  close: X,
  plus: Plus,
  delete: Trash2,
  edit: Pencil,
  copy: Copy,
  download: Download,
  upload: Upload,
  import: FileInput,
  export: FileOutput,
  refresh: RefreshCw,
  search: Search,
  settings: Settings2,
  sun: Sun,
  moon: Moon,
  logout: LogOut,
  server: Server,
  monitor: Monitor,
  file: FileCode2,
  key: KeyRound,
  link: Link,
  grip: GripVertical,
  up: ArrowUp,
  arrowDown: ArrowDown,
  more: MoreHorizontal,
  eye: Eye,
  eyeOff: EyeOff,
  loading: LoaderCircle,
  filter: SlidersHorizontal,
  external: ExternalLink,
  shield: Shield,
  users: Users,
  chart: ChartNoAxesCombined,
  network: Network,
  terminal: Terminal,
  save: Save,
  folder: FolderOpen,
  connect: Plug,
  disconnect: Unplug,
  reset: RotateCcw,
  all: CheckCheck,
  ban: Ban,
  menu: Menu,
  collapse: PanelLeftClose,
  expand: PanelLeftOpen,
}
export type IconName = keyof typeof icons
withDefaults(defineProps<{ name: IconName; size?: number }>(), { size: 16 })
</script>
<template>
  <component
    :is="icons[name]"
    :size="size"
    :stroke-width="1.7"
    aria-hidden="true"
    class="ui-icon"
    :class="{ 'ui-icon--spin': name === 'loading' }"
  />
</template>
<style scoped>
.ui-icon {
  flex: none;
  vertical-align: middle;
}

.ui-icon--spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .ui-icon--spin {
    animation: none;
  }
}
</style>
