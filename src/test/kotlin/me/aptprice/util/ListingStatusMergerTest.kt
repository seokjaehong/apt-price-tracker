package me.aptprice.util

import me.aptprice.model.Listing
import me.aptprice.model.MarketStatus
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

class ListingStatusMergerTest {

    @Test
    fun `same unit with new article number is treated as same active listing`() {
        val oldListing = listing(
            articleNo = "old-1",
            sameAddrHash = "hash-1",
            price = 120000,
            firstSeenAt = "2026-04-01T09:00:00",
            lastSeenAt = "2026-04-10T09:00:00"
        )
        val freshListing = listing(
            articleNo = "new-1",
            sameAddrHash = "hash-1",
            price = 120000
        )

        val result = ListingStatusMerger.merge(
            oldData = mapOf(oldListing.articleNo to oldListing),
            allNewListings = listOf(freshListing),
            successfulRegions = setOf(oldListing.regionName),
            now = "2026-04-11T09:00:00",
            offMarketConfirmMissCount = 3
        )

        assertEquals(1, result.mergedListings.size)
        val merged = result.mergedListings.single()
        assertEquals("old-1", merged.normalizedEntityId())
        assertEquals("new-1", merged.articleNo)
        assertEquals(MarketStatus.ACTIVE, merged.status)
        assertEquals("2026-04-01T09:00:00", merged.firstSeenAt)
        assertEquals("2026-04-11T09:00:00", merged.lastSeenAt)
        assertTrue(result.notifyList.isEmpty())
        assertFalse(result.mergedListings.any { it.articleNo == "old-1" })
    }

    @Test
    fun `same unit reappearing after off market becomes relisted even if article number changed`() {
        val oldListing = listing(
            articleNo = "old-2",
            sameAddrHash = "hash-2",
            status = MarketStatus.OFF_MARKET,
            statusChangedAt = "2026-04-10T09:00:00",
            offMarketAt = "2026-04-10T09:00:00",
            missCount = 3
        )
        val freshListing = listing(
            articleNo = "new-2",
            sameAddrHash = "hash-2"
        )

        val result = ListingStatusMerger.merge(
            oldData = mapOf(oldListing.articleNo to oldListing),
            allNewListings = listOf(freshListing),
            successfulRegions = setOf(oldListing.regionName),
            now = "2026-04-12T09:00:00",
            offMarketConfirmMissCount = 3
        )

        val merged = result.mergedListings.single()
        assertEquals("old-2", merged.normalizedEntityId())
        assertEquals("new-2", merged.articleNo)
        assertEquals(MarketStatus.RELISTED, merged.status)
        assertEquals("2026-04-12T09:00:00", merged.statusChangedAt)
        assertNull(merged.offMarketAt)
        assertEquals(listOf("다시 등록된 매물♻️"), result.notifyList.map { it.second })
        assertEquals(1, result.relistedChanged)
    }

    @Test
    fun `re-registered article with new hash matches same unit when description is identical`() {
        // 재등록 시 articleNo와 sameAddrHash가 모두 바뀌지만 동/층/면적 + 설명으로 같은 집을 이어붙인다.
        val oldListing = listing(
            articleNo = "old-3",
            sameAddrHash = "old-3",
            price = 150000,
            featureDesc = "남향 내부샤시 중문 거실확장",
            firstSeenAt = "2026-08-02T09:00:00",
            lastSeenAt = "2026-08-05T09:00:00"
        )
        val freshListing = listing(
            articleNo = "new-3",
            sameAddrHash = "new-3",
            price = 150000,
            featureDesc = "남향 내부샤시 중문 거실확장"
        )

        val result = ListingStatusMerger.merge(
            oldData = mapOf(oldListing.articleNo to oldListing),
            allNewListings = listOf(freshListing),
            successfulRegions = setOf(oldListing.regionName),
            now = "2026-08-06T09:00:00",
            offMarketConfirmMissCount = 3
        )

        assertEquals(1, result.mergedListings.size)
        val merged = result.mergedListings.single()
        assertEquals("old-3", merged.normalizedEntityId())
        assertEquals("new-3", merged.articleNo)
        assertEquals(MarketStatus.ACTIVE, merged.status)
        assertEquals("2026-08-02T09:00:00", merged.firstSeenAt)
        assertTrue(result.notifyList.isEmpty())
        assertEquals(0, result.offMarketCandidateChanged)
    }

    @Test
    fun `re-registered article with different description matches by close price and notifies price drop`() {
        val oldListing = listing(
            articleNo = "old-4",
            sameAddrHash = "old-4",
            price = 150000,
            featureDesc = "밝은 집 입주협의"
        )
        val freshListing = listing(
            articleNo = "new-4",
            sameAddrHash = "new-4",
            price = 147000,
            featureDesc = "가격 조정 급매"
        )

        val result = ListingStatusMerger.merge(
            oldData = mapOf(oldListing.articleNo to oldListing),
            allNewListings = listOf(freshListing),
            successfulRegions = setOf(oldListing.regionName),
            now = "2026-08-06T09:00:00",
            offMarketConfirmMissCount = 3
        )

        assertEquals(1, result.mergedListings.size)
        val merged = result.mergedListings.single()
        assertEquals("old-4", merged.normalizedEntityId())
        assertEquals(listOf("급매⬇️3000만"), result.notifyList.map { it.second })
    }

    @Test
    fun `same unit key with different description and far price is treated as new listing`() {
        val oldListing = listing(
            articleNo = "old-5",
            sameAddrHash = "old-5",
            price = 150000,
            featureDesc = "남향 수리된 집"
        )
        val freshListing = listing(
            articleNo = "new-5",
            sameAddrHash = "new-5",
            price = 165000,
            featureDesc = "북향 올수리"
        )

        val result = ListingStatusMerger.merge(
            oldData = mapOf(oldListing.articleNo to oldListing),
            allNewListings = listOf(freshListing),
            successfulRegions = setOf(oldListing.regionName),
            now = "2026-08-06T09:00:00",
            offMarketConfirmMissCount = 3
        )

        assertEquals(2, result.mergedListings.size)
        assertEquals(listOf("신규✨"), result.notifyList.map { it.second })
        val missing = result.mergedListings.single { it.normalizedEntityId() == "old-5" }
        assertEquals(MarketStatus.OFF_MARKET_CANDIDATE, missing.status)
        assertEquals(1, missing.missCount)
        assertEquals("2026-08-06T09:00:00", missing.updatedAt)
        assertEquals(1, result.offMarketCandidateChanged)
    }

    @Test
    fun `off market unit re-registered with new hash becomes relisted via unit match`() {
        val oldListing = listing(
            articleNo = "old-6",
            sameAddrHash = "old-6",
            price = 150000,
            featureDesc = "동일한 설명",
            status = MarketStatus.OFF_MARKET,
            statusChangedAt = "2026-08-07T09:00:00",
            offMarketAt = "2026-08-07T09:00:00",
            missCount = 3
        )
        val freshListing = listing(
            articleNo = "new-6",
            sameAddrHash = "new-6",
            price = 150000,
            featureDesc = "동일한 설명"
        )

        val result = ListingStatusMerger.merge(
            oldData = mapOf(oldListing.articleNo to oldListing),
            allNewListings = listOf(freshListing),
            successfulRegions = setOf(oldListing.regionName),
            now = "2026-08-10T09:00:00",
            offMarketConfirmMissCount = 3
        )

        val merged = result.mergedListings.single()
        assertEquals("old-6", merged.normalizedEntityId())
        assertEquals(MarketStatus.RELISTED, merged.status)
        assertNull(merged.offMarketAt)
        assertEquals(listOf("다시 등록된 매물♻️"), result.notifyList.map { it.second })
        assertEquals(1, result.relistedChanged)
    }

    @Test
    fun `direct article number match wins over identity fallback`() {
        // 살아 있는 매물(keep-1)의 이전 기록을, 같은 그룹 해시를 가진 다른 신규 매물이 가로채면 안 된다.
        val oldListing = listing(
            articleNo = "keep-1",
            sameAddrHash = "keep-1",
            price = 150000
        )
        val duplicateFresh = listing(
            articleNo = "other-1",
            sameAddrHash = "keep-1",
            price = 150000
        )
        val sameFresh = listing(
            articleNo = "keep-1",
            sameAddrHash = "keep-1",
            price = 150000
        )

        val result = ListingStatusMerger.merge(
            oldData = mapOf(oldListing.articleNo to oldListing),
            allNewListings = listOf(duplicateFresh, sameFresh),
            successfulRegions = setOf(oldListing.regionName),
            now = "2026-08-06T09:00:00",
            offMarketConfirmMissCount = 3
        )

        assertEquals(2, result.mergedListings.size)
        val kept = result.mergedListings.single { it.normalizedEntityId() == "keep-1" }
        assertEquals("keep-1", kept.articleNo)
        assertEquals("2026-04-01T09:00:00", kept.firstSeenAt)
        val added = result.mergedListings.single { it.normalizedEntityId() == "other-1" }
        assertEquals(MarketStatus.ACTIVE, added.status)
    }

    private fun listing(
        articleNo: String,
        sameAddrHash: String,
        price: Long = 100000,
        featureDesc: String = "남향",
        firstSeenAt: String = "2026-04-01T09:00:00",
        lastSeenAt: String = "2026-04-01T09:00:00",
        status: MarketStatus = MarketStatus.ACTIVE,
        statusChangedAt: String = firstSeenAt,
        offMarketAt: String? = null,
        missCount: Int = 0,
    ): Listing = Listing(
        articleNo = articleNo,
        hscpNo = "100",
        sameAddrHash = sameAddrHash,
        buildingName = "101동",
        title = "테스트아파트",
        featureDesc = featureDesc,
        tagList = listOf("급매"),
        regionName = "서울동작_흑석동",
        price = price,
        floor = "10/20",
        areaSqm = 84.0,
        areaSupplySqm = 112.0,
        areaExclusiveSqm = 84.0,
        pyeong = 34,
        url = "https://example.com/$articleNo",
        updatedAt = firstSeenAt,
        firstSeenAt = firstSeenAt,
        lastSeenAt = lastSeenAt,
        status = status,
        statusChangedAt = statusChangedAt,
        offMarketAt = offMarketAt,
        missCount = missCount
    )
}
