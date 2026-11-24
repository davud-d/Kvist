package site.kvist.cloud

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AppSignInTest {
    private val cloud = "https://cloud.kvist.site"

    @Test
    fun aNonceIsLongHexAndNeverRepeats() {
        val first = AppSignIn.makeNonce()
        assertTrue(Regex("[0-9a-f]{64}").matches(first))
        assertNotEquals(first, AppSignIn.makeNonce())
    }

    @Test
    fun theStartAddressIsTheClouds() {
        assertEquals("https://cloud.kvist.site/auth/app/start?nonce=abc", AppSignIn.startUrl(cloud, "abc"))
    }

    @Test
    fun theCodeIsReadOnlyFromTheAppsOwnAddress() {
        assertEquals("A.B.C", AppSignIn.codeFrom("kvistcloud://signed-in?code=A.B.C"))
        assertEquals("A.B-C_d", AppSignIn.codeFrom("kvistcloud://signed-in?x=1&code=A.B-C_d"))
        assertNull(AppSignIn.codeFrom("kvistcloud://other?code=A"))
        assertNull(AppSignIn.codeFrom("https://evil.example/signed-in?code=A"))
        assertNull(AppSignIn.codeFrom("kvistcloud://signed-in"))
        assertNull(AppSignIn.codeFrom("kvistcloud://signed-in?code="))
        assertNull(AppSignIn.codeFrom("not a url"))
        assertNull(AppSignIn.codeFrom(null))
    }

    @Test
    fun theExchangeCarriesTheCodeTheNonceAndWhereToLand() {
        assertEquals(
            "https://cloud.kvist.site/auth/app/exchange?code=A.B-C_d&nonce=n1&next=%2Fm%2F",
            AppSignIn.exchangeUrl(cloud, "A.B-C_d", "n1"),
        )
    }
}
