package me.aptprice

import org.junit.jupiter.api.Test
import org.springframework.boot.test.context.SpringBootTest

// daebang.enabled까지 꺼야 컨텍스트 로드 테스트가 실제 네이버 수집을 실행하지 않는다.
@SpringBootTest(properties = ["bot.enabled=false", "daebang.enabled=false"])
class AptpriceApplicationTests {

    @Test
    fun contextLoads() {
    }

}
