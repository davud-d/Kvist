package com.kvist.intellij.completion

import com.intellij.ide.util.PropertiesComponent

/**
 * The one user-facing knob for inline completion: on/off. Kept in the IDE's
 * application properties, the same lightweight persistence [com.kvist.intellij.chat.KvistEnvironment]
 * uses for `kvist.environment` — a full Settings page for a single boolean would be
 * disproportionate, so this is exposed instead through a Tools-menu toggle action.
 *
 * The debounce delay is not a user setting: nobody asked for it to be tunable, and a
 * constant is simpler than a preference nobody can act on without profiling first.
 */
internal object KvistCompletionSettings {
    private const val ENABLED_KEY = "kvist.inlineCompletion.enabled"
    private const val DEFAULT_ENABLED = true

    var isEnabled: Boolean
        get() = PropertiesComponent.getInstance().getBoolean(ENABLED_KEY, DEFAULT_ENABLED)
        set(value) = PropertiesComponent.getInstance().setValue(ENABLED_KEY, value, DEFAULT_ENABLED)
}
