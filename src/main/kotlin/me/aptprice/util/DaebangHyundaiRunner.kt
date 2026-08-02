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
    @Value("\${daebang.target-complex-no:368}") private val targetComplexNo: String,
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
            val normalizedTitle = normalizeName(it.title)
            it.hscpNo == targetComplexNo &&
                normalizedTitle.contains(normalizedTarget) &&
                it.pyeong in minPyeong..maxPyeong
        }

        if (fetched.isNotEmpty() && targetListings.isEmpty()) {
            val available = fetched
                .map { "${it.title}(${it.pyeong}평)" }
                .distinct()
                .sorted()
                .joinToString(", ")
                .take(2000)
            error(
                "대상 단지를 찾지 못했습니다. " +
                    "target=$targetComplexName, normalizedTarget=$normalizedTarget, " +
                    "range=${minPyeong}..${maxPyeong}, available=$available"
            )
        }

        // 원본 저장소의 다른 단지 데이터가 남아 있어도 대방현대1차 이력에 합쳐지지 않게 한다.
        val oldData = repository.loadAll().filterValues { it.hscpNo == targetComplexNo }
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
