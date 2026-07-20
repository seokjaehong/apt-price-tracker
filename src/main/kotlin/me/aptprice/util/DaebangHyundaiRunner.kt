package me.aptprice.util

import me.aptprice.repository.FileDataRepository
import me.aptprice.service.NaverService
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.annotation.Value
import org.springframework.boot.CommandLineRunner
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.stereotype.Component
import java.time.LocalDateTime

@Component
@ConditionalOnProperty(name = ["daebang.enabled"], havingValue = "true")
class DaebangHyundaiRunner(
    private val naverService: NaverService,
    private val repository: FileDataRepository,
    @Value("\${daebang.target-complex-name:대방현대1차}") private val targetComplexName: String,
    @Value("\${daebang.region-name:서울동작_대방동}") private val regionName: String,
    @Value("\${daebang.cortar-no:1159010800}") private val cortarNo: String,
    @Value("\${daebang.min-pyeong:20}") private val minPyeong: Int,
    @Value("\${daebang.max-pyeong:39}") private val maxPyeong: Int,
    @Value("\${daebang.off-market-confirm-miss-count:3}") private val offMarketConfirmMissCount: Int,
) : CommandLineRunner {
    private val log = LoggerFactory.getLogger(javaClass)

    override fun run(vararg args: String) {
        val now = LocalDateTime.now().toString()
        val normalizedTarget = normalizeName(targetComplexName)
        val fetched = naverService.fetchListings(regionName, cortarNo)
        val targetListings = fetched.filter {
            normalizeName(it.title) == normalizedTarget && it.pyeong in minPyeong..maxPyeong
        }

        if (fetched.isNotEmpty() && targetListings.isEmpty()) {
            val available = fetched.map { it.title }.distinct().sorted().joinToString(", ").take(1000)
            error("대상 단지를 찾지 못했습니다. target=$targetComplexName, available=$available")
        }

        val oldData = repository.loadAll()
        val merged = ListingStatusMerger.merge(
            oldData = oldData,
            allNewListings = targetListings,
            successfulRegions = setOf(regionName),
            now = now,
            offMarketConfirmMissCount = offMarketConfirmMissCount,
        )
        repository.saveAll(merged.mergedListings)

        log.info(
            "대방현대1차 수집 완료 - 현재 노출: {}, 삭제 후보 전환: {}, 거래종결 추정 전환: {}, 재등록 전환: {}, 전체 저장: {}",
            merged.newVisibleCount,
            merged.offMarketCandidateChanged,
            merged.offMarketChanged,
            merged.relistedChanged,
            merged.mergedListings.size,
        )
    }

    private fun normalizeName(value: String): String = value
        .lowercase()
        .replace(Regex("[^0-9a-z가-힣]"), "")
}
