package me.aptprice.util

import me.aptprice.model.Listing
import me.aptprice.model.MarketStatus
import java.util.Locale
import kotlin.math.abs
import kotlin.math.max

internal object ListingStatusMerger {

    // UNIT 키 재등록 매칭 보조 조건: 설명 동일 또는 호가 차이 5% 이내
    private const val RE_REGISTRATION_PRICE_TOLERANCE = 0.05

    fun merge(
        oldData: Map<String, Listing>,
        allNewListings: List<Listing>,
        successfulRegions: Set<String>,
        now: String,
        offMarketConfirmMissCount: Int,
    ): MergeResult {
        val newByArticleNo = allNewListings
            .groupBy { it.articleNo }
            .mapValues { (_, items) -> items.maxByOrNull { it.updatedAt } ?: items.first() }

        val oldByEntityId = oldData.mapKeys { it.key.ifBlank { it.value.normalizedEntityId() } }
        val oldByArticleNo = oldByEntityId.values.associateBy { it.articleNo }
        val oldByHashKey = buildOldIndex(oldByEntityId.values, ::hashIdentityKey)
        val oldByUnitKey = buildOldIndex(oldByEntityId.values, ::unitIdentityKey)

        val matchedOldEntityIds = mutableSetOf<String>()
        val matchedOldByArticleNo = mutableMapOf<String, Listing>()

        // 1차: articleNo 직접 매칭을 전부 먼저 확정해, 아래 식별 키 매칭이
        // 아직 살아 있는 매물의 이전 기록을 가로채지 못하게 한다.
        newByArticleNo.keys.forEach { articleNo ->
            val direct = oldByArticleNo[articleNo] ?: return@forEach
            if (matchedOldEntityIds.add(direct.normalizedEntityId())) {
                matchedOldByArticleNo[articleNo] = direct
            }
        }

        // 2차: 남은 매물을 동일주소 그룹 해시 → 동/층/면적(UNIT) 키 순으로 매칭한다.
        // 재등록 시 articleNo와 해시가 모두 바뀌므로 UNIT 키까지 봐야 같은 집을 이어붙일 수 있다.
        newByArticleNo.forEach { (articleNo, freshListing) ->
            if (matchedOldByArticleNo.containsKey(articleNo)) return@forEach
            val matched = claimByHash(freshListing, oldByHashKey, matchedOldEntityIds)
                ?: claimByUnit(freshListing, oldByUnitKey, matchedOldEntityIds)
            if (matched != null) {
                matchedOldByArticleNo[articleNo] = matched
            }
        }

        val mergedByEntityId = mutableMapOf<String, Listing>()
        val notifyList = mutableListOf<Pair<Listing, String>>()
        var offMarketCandidateChanged = 0
        var offMarketChanged = 0
        var relistedChanged = 0

        newByArticleNo.forEach { (articleNo, freshListing) ->
            val oldListing = matchedOldByArticleNo[articleNo]
            val normalizedListing = mergeSeenListing(oldListing, freshListing, now)
            mergedByEntityId[normalizedListing.normalizedEntityId()] = normalizedListing

            if (oldListing == null) {
                notifyList.add(normalizedListing to "신규✨")
            } else if (normalizedListing.status == MarketStatus.RELISTED) {
                relistedChanged += 1
                notifyList.add(normalizedListing to "다시 등록된 매물♻️")
            } else if (freshListing.price < oldListing.price) {
                notifyList.add(normalizedListing to "급매⬇️${oldListing.price - freshListing.price}만")
            }
        }

        oldByEntityId.forEach { (entityId, oldListing) ->
            if (entityId in matchedOldEntityIds) return@forEach
            if (newByArticleNo.containsKey(oldListing.articleNo)) return@forEach

            if (oldListing.regionName !in successfulRegions) {
                mergedByEntityId[entityId] = oldListing
                return@forEach
            }

            val transitioned = transitionMissingListing(oldListing, now, offMarketConfirmMissCount)
            mergedByEntityId[entityId] = transitioned

            if (transitioned.status != oldListing.status) {
                when (transitioned.status) {
                    MarketStatus.OFF_MARKET_CANDIDATE -> offMarketCandidateChanged += 1
                    MarketStatus.OFF_MARKET -> offMarketChanged += 1
                    else -> Unit
                }
            }
        }

        val mergedListings = mergedByEntityId.values
            .sortedWith(compareBy<Listing> { it.regionName }.thenBy { it.normalizedEntityId() })

        return MergeResult(
            mergedListings = mergedListings,
            notifyList = notifyList,
            newVisibleCount = newByArticleNo.size,
            offMarketCandidateChanged = offMarketCandidateChanged,
            offMarketChanged = offMarketChanged,
            relistedChanged = relistedChanged
        )
    }

    private fun buildOldIndex(
        oldListings: Collection<Listing>,
        keyOf: (Listing) -> String?,
    ): Map<String, List<Listing>> {
        val index = mutableMapOf<String, MutableList<Listing>>()
        oldListings.forEach { listing ->
            val key = keyOf(listing) ?: return@forEach
            index.getOrPut(key) { mutableListOf() }.add(listing)
        }
        return index
    }

    private fun claimByHash(
        freshListing: Listing,
        oldByHashKey: Map<String, List<Listing>>,
        matchedOldEntityIds: MutableSet<String>,
    ): Listing? {
        val key = hashIdentityKey(freshListing) ?: return null
        val matched = oldByHashKey[key]
            ?.firstOrNull { it.normalizedEntityId() !in matchedOldEntityIds }
            ?: return null
        matchedOldEntityIds += matched.normalizedEntityId()
        return matched
    }

    private fun claimByUnit(
        freshListing: Listing,
        oldByUnitKey: Map<String, List<Listing>>,
        matchedOldEntityIds: MutableSet<String>,
    ): Listing? {
        val key = unitIdentityKey(freshListing) ?: return null
        val candidates = oldByUnitKey[key]
            ?.filter { it.normalizedEntityId() !in matchedOldEntityIds }
        if (candidates.isNullOrEmpty()) return null
        val matched = pickReRegistrationMatch(freshListing, candidates) ?: return null
        matchedOldEntityIds += matched.normalizedEntityId()
        return matched
    }

    // 같은 동/층/면적이어도 다른 호수일 수 있으므로,
    // 설명 동일 → 호가 근접(5% 이내, 차이 최소) 순으로만 재등록으로 인정한다.
    private fun pickReRegistrationMatch(freshListing: Listing, candidates: List<Listing>): Listing? {
        val freshDescKey = normalizeDescKey(freshListing.featureDesc)
        if (freshDescKey.isNotBlank()) {
            candidates.firstOrNull { normalizeDescKey(it.featureDesc) == freshDescKey }?.let { return it }
        }
        return candidates
            .filter { isPriceClose(freshListing.price, it.price) }
            .minByOrNull { abs(it.price - freshListing.price) }
    }

    private fun isPriceClose(a: Long, b: Long): Boolean {
        if (a <= 0L || b <= 0L) return false
        return abs(a - b) <= max(a, b) * RE_REGISTRATION_PRICE_TOLERANCE
    }

    private fun mergeSeenListing(old: Listing?, fresh: Listing, now: String): Listing {
        if (old == null) {
            return fresh.copy(
                entityId = fresh.normalizedEntityId(),
                updatedAt = now,
                firstSeenAt = now,
                lastSeenAt = now,
                status = MarketStatus.ACTIVE,
                statusChangedAt = now,
                offMarketAt = null,
                missCount = 0
            )
        }

        val nextStatus = if (old.status == MarketStatus.OFF_MARKET) {
            MarketStatus.RELISTED
        } else {
            MarketStatus.ACTIVE
        }
        val statusChangedAt = if (nextStatus != old.status) now else old.statusChangedAt
        val normalizedFirstSeenAt = old.firstSeenAt.ifBlank { old.updatedAt }
        val normalizedBuildingName = fresh.buildingName.ifBlank { old.buildingName }
        val normalizedFeatureDesc = fresh.featureDesc.ifBlank { old.featureDesc }
        val normalizedTagList = fresh.tagList.ifEmpty { old.tagList }

        return fresh.copy(
            entityId = old.normalizedEntityId(),
            updatedAt = now,
            firstSeenAt = normalizedFirstSeenAt,
            lastSeenAt = now,
            buildingName = normalizedBuildingName,
            featureDesc = normalizedFeatureDesc,
            tagList = normalizedTagList,
            status = nextStatus,
            statusChangedAt = statusChangedAt,
            offMarketAt = null,
            missCount = 0
        )
    }

    private fun transitionMissingListing(old: Listing, now: String, offMarketConfirmMissCount: Int): Listing {
        if (old.status == MarketStatus.OFF_MARKET) {
            return old
        }

        val newMissCount = old.missCount + 1
        val threshold = offMarketConfirmMissCount.coerceAtLeast(2)
        val nextStatus = if (newMissCount >= threshold) {
            MarketStatus.OFF_MARKET
        } else {
            MarketStatus.OFF_MARKET_CANDIDATE
        }
        val statusChangedAt = if (nextStatus != old.status) now else old.statusChangedAt

        return old.copy(
            updatedAt = now,
            status = nextStatus,
            statusChangedAt = statusChangedAt,
            offMarketAt = if (nextStatus == MarketStatus.OFF_MARKET) (old.offMarketAt ?: now) else old.offMarketAt,
            missCount = newMissCount
        )
    }

    private fun hashIdentityKey(listing: Listing): String? =
        listing.sameAddrHash.takeIf { it.isNotBlank() }?.let { "HASH:${listing.hscpNo}:$it" }

    private fun unitIdentityKey(listing: Listing): String? {
        val buildingKey = normalizeBuildingKey(listing.buildingName)
        val floorKey = normalizeFloorKey(listing.floor)
        val areaKey = normalizedAreaKey(
            sequenceOf(listing.areaExclusiveSqm, listing.areaSupplySqm, listing.areaSqm)
                .firstOrNull { it > 0.0 } ?: 0.0
        )
        val titleKey = normalizeTitleKey(listing.title)
        if (buildingKey.isBlank() || floorKey.isBlank() || areaKey <= 0.0 || titleKey.isBlank()) {
            return null
        }
        return "UNIT:${listing.hscpNo}:$titleKey:$buildingKey:$floorKey:${"%.2f".format(Locale.US, areaKey)}"
    }

    private fun normalizeDescKey(raw: String): String =
        raw.lowercase().replace(Regex("\\s+"), "")

    private fun normalizeFloorKey(raw: String): String {
        if (raw.isBlank()) return ""
        return raw
            .replace(" ", "")
            .replace("저", "L")
            .replace("중", "M")
            .replace("고", "H")
            .uppercase()
    }

    private fun normalizeTitleKey(raw: String): String {
        if (raw.isBlank()) return ""
        return raw
            .lowercase()
            .replace(Regex("\\s+"), "")
            .replace(Regex("[^0-9a-z가-힣동]"), "")
    }

    private fun normalizeBuildingKey(raw: String): String {
        if (raw.isBlank()) return ""
        return raw
            .replace(" ", "")
            .replace(Regex("[^0-9a-zA-Z가-힣동]"), "")
            .uppercase()
    }

    private fun normalizedAreaKey(area: Double): Double =
        if (area > 0.0) kotlin.math.round(area * 100.0) / 100.0 else 0.0
}

internal data class MergeResult(
    val mergedListings: List<Listing>,
    val notifyList: List<Pair<Listing, String>>,
    val newVisibleCount: Int,
    val offMarketCandidateChanged: Int,
    val offMarketChanged: Int,
    val relistedChanged: Int
)
