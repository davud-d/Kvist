package site.kvist.cloud

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class OriginsTest {
    private val cloud = "https://cloud.kvist.site"

    @Test
    fun anOriginIsSchemeHostAndPortWithoutTheStandardOnes() {
        assertEquals("https://cloud.kvist.site", Origins.originOf("https://cloud.kvist.site/m/s/abc?x=1"))
        assertEquals("https://cloud.kvist.site", Origins.originOf("https://CLOUD.kvist.site:443/"))
        assertEquals("http://10.0.2.2:8742", Origins.originOf("http://10.0.2.2:8742/m/"))
        assertNull(Origins.originOf("intent://x#Intent;end"))
        assertNull(Origins.originOf("not a url"))
        assertNull(Origins.originOf("file:///sdcard/x.html"))
    }

    @Test
    fun theCloudAndOnlyTheCloudIsTheCloud() {
        assertTrue(Origins.isCloud("https://cloud.kvist.site/m/", cloud))
        assertFalse(Origins.isCloud("https://cloud.kvist.site.evil.example/m/", cloud))
        assertFalse(Origins.isCloud("http://cloud.kvist.site/m/", cloud))
        assertFalse(Origins.isCloud("https://cloud.kvist.site:8443/m/", cloud))
        assertFalse(Origins.isCloud("https://evil.example/https://cloud.kvist.site/", cloud))
        assertFalse(Origins.isCloud("https://cloud.kvist.site@evil.example/", cloud))
    }

    @Test
    fun githubStaysInTheAppOnlyForSigningIn() {
        assertTrue(Origins.staysInApp("https://github.com/login/oauth/authorize?client_id=x", cloud))
        assertTrue(Origins.staysInApp("https://cloud.kvist.site/auth/github/callback?code=1", cloud))
        assertFalse(Origins.staysInApp("http://github.com/login", cloud))
        assertFalse(Origins.staysInApp("https://github.com.evil.example/login", cloud))
        assertFalse(Origins.staysInApp("https://gist.github.com/x", cloud))
        assertFalse(Origins.staysInApp("https://pay.example/checkout", cloud))
    }

    @Test
    fun onlyWebLinksLeaveForOtherApps() {
        assertTrue(Origins.mayOpenOutside("https://platega.example/pay?id=1"))
        assertTrue(Origins.mayOpenOutside("mailto:support@cloud.example"))
        assertFalse(Origins.mayOpenOutside("intent://scan/#Intent;scheme=zxing;end"))
        assertFalse(Origins.mayOpenOutside("file:///data/data/site.kvist.cloud/x"))
        assertFalse(Origins.mayOpenOutside("javascript:alert(1)"))
        assertFalse(Origins.mayOpenOutside("content://sms/inbox"))
        assertFalse(Origins.mayOpenOutside(""))
    }

    @Test
    fun aTestBuildMayPointAtAPlainHttpServer() {
        val test = "http://10.0.2.2:8742"
        assertTrue(Origins.staysInApp("http://10.0.2.2:8742/m/s/x", test))
        assertFalse(Origins.staysInApp("http://10.0.2.2:9999/m/", test))
    }
}
