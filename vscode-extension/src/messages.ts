import type { CoreEvent } from '../../src/protocol';

export interface ProviderInfo {
    id: string;
    label: string;
    custom: boolean;
    has_credentials: boolean;
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
    | { type: 'showSettings' };

export type ToHost =
    | { type: 'ready' }
    | { type: 'submit'; text: string }
    | { type: 'command'; name: string; args: string[] }
    | { type: 'respond'; requestId: string; answer: boolean | string | null; remember: boolean }
    | { type: 'cancel' }
    | { type: 'pickFiles' }
    | { type: 'getConfig' }
    | { type: 'getEnvironment' }
    | { type: 'setEnvironment'; env: Record<string, string> }
    | { type: 'getSessions' }
    | { type: 'openFile'; path: string; line: number | null }
    | { type: 'openLink'; url: string };
