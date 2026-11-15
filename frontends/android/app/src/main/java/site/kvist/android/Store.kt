package site.kvist.android

import android.content.Context
import java.util.UUID

/** Remembers this phone's identity and the one computer that trusts it. App-private storage, kept out of backups by the manifest. */
class Store(context: Context) {
    private val prefs = context.getSharedPreferences("kvist", Context.MODE_PRIVATE)

    /** Made once and never changed: it is how a computer tells this phone from another. */
    fun deviceId(): String {
        prefs.getString("device", null)?.let { return it }
        val fresh = UUID.randomUUID().toString()
        prefs.edit().putString("device", fresh).apply()
        return fresh
    }

    fun load(): Saved? {
        val host = prefs.getString("host", null) ?: return null
        val token = prefs.getString("token", null) ?: return null
        return Saved(prefs.getString("id", host) ?: host, prefs.getString("name", host) ?: host, host, prefs.getInt("port", 9671), token)
    }

    fun save(saved: Saved) {
        prefs.edit()
            .putString("id", saved.id).putString("name", saved.name).putString("host", saved.host)
            .putInt("port", saved.port).putString("token", saved.token)
            .apply()
    }

    /** Forgets the computer, not the phone's own identity. */
    fun forget() {
        prefs.edit().remove("id").remove("name").remove("host").remove("port").remove("token").apply()
    }
}
