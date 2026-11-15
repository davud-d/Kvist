package site.kvist.android

import org.junit.Assert.assertEquals
import org.junit.Test

class PairingTest {
    @Test
    fun theHeaderCarriesTheTokenAsTheBasicPassword() {
        // "kvist:abc123" in base64; the computer reads the part after the colon.
        assertEquals("Basic a3Zpc3Q6YWJjMTIz", Pairing.authHeader("abc123"))
    }

    @Test
    fun readsEveryAnswerTheComputerCanGive() {
        assertEquals(Answer.Allowed("tok"), Pairing.parseAnswer("""{"ok":true,"state":"approved","token":"tok"}"""))
        assertEquals(Answer.Pending, Pairing.parseAnswer("""{"ok":true,"state":"pending"}"""))
        assertEquals(Answer.Denied, Pairing.parseAnswer("""{"ok":true,"state":"denied"}"""))
        assertEquals(Answer.Unavailable, Pairing.parseAnswer("""{"ok":true,"state":"unavailable"}"""))
        assertEquals(Answer.Expired, Pairing.parseAnswer("""{"ok":true,"state":"expired"}"""))
    }

    @Test
    fun anApprovalWithoutATokenIsNotAnApproval() {
        // The token travels once; a second reading of the same answer has none.
        assertEquals(Answer.Expired, Pairing.parseAnswer("""{"ok":true,"state":"approved"}"""))
        assertEquals(Answer.Expired, Pairing.parseAnswer("not json"))
    }
}
