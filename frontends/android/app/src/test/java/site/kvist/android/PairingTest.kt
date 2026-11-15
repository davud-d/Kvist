package site.kvist.android

import org.junit.Assert.assertEquals
import org.junit.Test

class PairingTest {
    @Test
    fun theCodeIsReadAsTypedOnAPhone() {
        assertEquals("K7QD4M2X9RTB", Pairing.normalize("k7qd-4m2x 9rtb"))
    }

    @Test
    fun theHeaderCarriesTheCodeAsTheBasicPassword() {
        // "kvist:K7QD4M2X9RTB" in base64; the server reads the part after the colon.
        assertEquals("Basic a3Zpc3Q6SzdRRDRNMlg5UlRC", Pairing.authHeader("K7QD4M2X9RTB"))
    }
}
