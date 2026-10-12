import type { CoreEvent, JournalEvent } from '../../../tui/ink/protocol';

export interface ProviderInfo {
    id: string;
    label: string;
    custom: boolean;
    has_credentials: boolean;
    auth_type?: "oauth" | "api_key";
    requires_manual_models: boolean;
}

export interface ConfigInfo {
    saved_provider: string;
    saved_role: string;
    providers: ProviderInfo[];
}

export interface SessionItem {
    name: string;
    title: string;
    updated_at: number;
}

export interface SessionInfo {
    active_session?: string;
    session_items?: SessionItem[];
}

export type ToWebview =
    | { type: 'event'; event: CoreEvent }
    | { type: 'exit'; expected: boolean; code: number | null }
    | { type: 'diagnostic'; text: string }
    | { type: 'fault'; message: string }
    | { type: 'config'; info: ConfigInfo }
    | { type: 'sessions'; info: SessionInfo }
    | { type: 'invoke'; name: string; args: string[] }
    | { type: 'attached'; paths: string[] }
    | { type: 'environment'; env: Record<string, string> }
    | { type: 'history'; name: string; events: JournalEvent[]; hasMore: boolean }
    | { type: 'needsSetup'; providers: ProviderInfo[] }
    | { type: 'setupResult'; ok: boolean; error?: string }
    | { type: 'modelList'; provider: string; ok: boolean; models: string[]; error?: string }
    | { type: 'registrationOpened'; provider: string; opened: boolean; url: string | null }
    | { type: 'showSettings' };

export type ToHost =
    | { type: 'ready' }
    | { type: 'submit'; text: string }
    | { type: 'command'; name: string; args: string[] }
    | { type: 'respond'; requestId: string; answer: boolean | string | null; remember: boolean }
    | { type: 'cancel' }
    | { type: 'pause' }
    | { type: 'stopHelper'; agentId: string }
    | { type: 'pickFiles' }
    | { type: 'getConfig' }
    | { type: 'getEnvironment' }
    | { type: 'setEnvironment'; env: Record<string, string> }
    | { type: 'getSessions' }
    | { type: 'getHistory'; name: string }
    | { type: 'setProvider'; provider: string; apiKey: string; model: string }
    | { type: 'listModels'; provider: string; apiKey: string }
    | { type: 'openRegistration'; provider: string }
    | { type: 'openFile'; path: string; line: number | null }
    | { type: 'openLink'; url: string };
