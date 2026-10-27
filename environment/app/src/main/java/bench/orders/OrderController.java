package bench.orders;

import java.util.Map;
import javax.validation.Valid;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api")
public class OrderController {
    private final OrderService service;
    private final JdbcTemplate jdbc;

    /**
     * 注入订单同步服务及健康检查所需的数据源。
     * @param service 订单同步服务
     * @param jdbc 数据库健康检查使用的数据访问对象
     */
    public OrderController(OrderService service, JdbcTemplate jdbc) {
        this.service = service;
        this.jdbc = jdbc;
    }

    /**
     * 接收单张外部订单，成功时返回其持久化主键。
     * @param request 本次同步的订单
     * @return 包含订单主键的响应
     */
    @PostMapping("/orders/sync")
    public Map<String, Long> sync(@Valid @RequestBody OrderRequest request) {
        return Map.of("id", service.sync(request));
    }

    /**
     * 确认当前进程与数据库连接均已就绪。
     * @return 服务和数据库的就绪状态
     */
    @GetMapping("/health")
    public Map<String, String> health() {
        jdbc.queryForObject("SELECT 1", Integer.class);
        return Map.of("status", "UP");
    }
}
