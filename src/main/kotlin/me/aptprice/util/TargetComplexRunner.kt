package me.aptprice.util

import me.aptprice.model.Listing
import me.aptprice.repository.FileDataRepository
import me.aptprice.service.AbuseBlockedException
import me.aptprice.service.NaverService
import me.aptprice.service.RegionFetchFailedException
import org.slf4j.LoggerFactory
import org.springframework.beans.factory.annotation.Value
import org.springframework.boot.CommandLineRunner
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty
import org.springframework.core.io.ClassPathResource
import org.springframework.stereotype.Component
import tools.jackson.databind.ObjectMapper
import java.time.LocalDateTime
import kotlin.random.Random

/**
 * 지정한 단지 목록만 수집한다.
 *
 * 대상은 리소스 JSON(targets.file)으로 관리한다. 동 전체를 긁지 않고 그 안에서
 * 고른 단지만 보기 때문에, 세대수 같은 기준으로 추린 목록을 그대로 쓸 수 있다.
 */
@Component
@ConditionalOnProperty(name = ["targets.enabled"], havingValue = "true")
class TargetComplexRunner(
    private val naverService: NaverService,
    private val repository: FileDataRepository,
    private val objectMapper: ObjectMapper,
    @Value("\${targets.file:targets/dongjak-500.json}") private val targetFile: String,
    @Value("\${targets.min-pyeong:20}") private val minPyeong: Int,
    @Value("\${targets.max-pyeong:39}") private val maxPyeong: Int,
    @Value("\${targets.off-market-confirm-miss-count:3}") private val offMarketConfirmMissCount: Int,
    @Value("\${targets.region-delay-min-ms:5000}") private val regionDelayMinMs: Long,
    @Value("\${targets.region-delay-max-ms:9000}") private val regionDelayMaxMs: Long,
    @Value("\${targets.stop-on-abuse:true}") private val stopOnAbuse: Boolean,
) : CommandLineRunner {

    private val log = LoggerFactory.getLogger(javaClass)
    private val random = Random(System.currentTimeMillis())

    override fun run(vararg args: String) {
        val spec = loadSpec()
        val allTargetNos = spec.regions.flatMap { r -> r.complexes.map { it.no } }.toSet()
        log.info(
            "=== 지정 단지 수집 시작: {} (지역 {}개, 단지 {}개) ===",
            spec.description,
            spec.regions.size,
            allTargetNos.size,
        )

        val now = LocalDateTime.now().toString()
        val collected = mutableListOf<Listing>()
        val successfulRegions = mutableSetOf<String>()
        var abortedByAbuse = false

        for ((index, region) in spec.regions.withIndex()) {
            val wanted = region.complexes.map { it.no }.toSet()
            try {
                val fetched = naverService.fetchListings(region.name, region.cortarNo, wanted)
                // 대상 단지 외 결과가 섞이지 않도록 한 번 더 거른다(동명 단지 방어).
                val filtered = fetched.filter { it.hscpNo in wanted && it.pyeong in minPyeong..maxPyeong }
                collected += filtered
                successfulRegions += region.name
                log.info(
                    "[{}/{}] {} 수집 완료 - 대상 단지 {}개, 매물 {}건",
                    index + 1, spec.regions.size, region.name, wanted.size, filtered.size,
                )
            } catch (e: AbuseBlockedException) {
                // 차단된 지역은 successfulRegions에 넣지 않아, 이번 회차 상태 전환에서 제외한다.
                log.warn("[{}/{}] {} 차단 감지: {}", index + 1, spec.regions.size, region.name, e.message)
                if (stopOnAbuse) {
                    abortedByAbuse = true
                    break
                }
            } catch (e: RegionFetchFailedException) {
                log.warn("[{}/{}] {} 수집 실패: {}", index + 1, spec.regions.size, region.name, e.message)
            }

            if (index < spec.regions.lastIndex) {
                Thread.sleep(randomDelayMs())
            }
        }

        if (successfulRegions.isEmpty()) {
            log.error("수집 성공한 지역이 없어 저장을 건너뜁니다. (차단 중단={})", abortedByAbuse)
            return
        }

        // 대상에서 빠진 단지의 과거 기록이 남아 있어도 이번 이력에 섞이지 않게 한다.
        val oldData = repository.loadAll().filterValues { it.hscpNo in allTargetNos }
        val merged = ListingStatusMerger.merge(
            oldData = oldData,
            allNewListings = collected,
            successfulRegions = successfulRegions,
            now = now,
            offMarketConfirmMissCount = offMarketConfirmMissCount,
        )
        repository.saveAll(merged.mergedListings)

        log.info(
            "지정 단지 수집 완료 - 지역 {}/{} 성공, 현재 노출: {}, 삭제 후보 전환: {}, 거래종결 추정 전환: {}, 재등록 전환: {}, 전체 저장: {}",
            successfulRegions.size,
            spec.regions.size,
            merged.newVisibleCount,
            merged.offMarketCandidateChanged,
            merged.offMarketChanged,
            merged.relistedChanged,
            merged.mergedListings.size,
        )
        if (abortedByAbuse) {
            log.warn("차단으로 일부 지역을 건너뛰었습니다. 남은 지역은 다음 실행에서 수집됩니다.")
        }
    }

    private fun loadSpec(): TargetSpec {
        val resource = ClassPathResource(targetFile)
        if (!resource.exists()) {
            error("수집 대상 파일을 찾을 수 없습니다: $targetFile")
        }
        val spec = resource.inputStream.use { objectMapper.readValue(it, TargetSpec::class.java) }
        if (spec.regions.isEmpty()) {
            error("수집 대상 파일에 지역이 없습니다: $targetFile")
        }
        return spec
    }

    private fun randomDelayMs(): Long {
        val min = regionDelayMinMs.coerceAtLeast(0L)
        val max = regionDelayMaxMs.coerceAtLeast(min)
        return if (max == min) min else random.nextLong(min, max + 1)
    }

    data class TargetSpec(
        val description: String = "",
        val minHouseholds: Int = 0,
        val regions: List<TargetRegion> = emptyList(),
    )

    data class TargetRegion(
        val name: String = "",
        val cortarNo: String = "",
        val complexes: List<TargetComplex> = emptyList(),
    )

    data class TargetComplex(
        val no: String = "",
        val name: String = "",
        val households: Int = 0,
    )
}
