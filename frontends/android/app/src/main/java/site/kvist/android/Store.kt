package site.kvist.android

import android.content.Context

/** Remembers the one paired computer. App-private storage, and the manifest keeps it out of backups. */
class Store(context: Context) {
    private val prefs = context.getSharedPreferences("kvist", Context.MODE_PRIVATE)

    fun load(): Saved? {
        val host = prefs.getString("host", null) ?: return null
        val code = prefs.getString("code", null) ?: return null
        return Saved(prefs.getString("name", host) ?: host, host, prefs.getInt("port", 9671), code)
    }

    fun save(saved: Saved) {
        prefs.edit()
            .putString("name", saved.name).putString("host", saved.host)
            .putInt("port", saved.port).putString("code", saved.code)
            .apply()
    }

    fun clear() {
        prefs.edit().clear().apply()
    }
}
