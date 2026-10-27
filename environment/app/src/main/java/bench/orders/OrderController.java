package bench.orders;

import java.util.List;
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
     * 注入业务服务与健康检查数据源。
     * @param service 订单服务
     * @param jdbc 数据访问对象
     */
    public OrderController(OrderService service, JdbcTemplate jdbc) {
        this.service = service;
        this.jdbc = jdbc;
    }

    /**
     * 同步单张订单，不触发自动排产。
     * @param request 订单内容
     * @return 订单主键
     */
    @PostMapping("/orders/sync")
    public Map<String, Long> sync(@Valid @RequestBody OrderRequest request) {
        return Map.of("id", service.sync(request));
    }

    /**
     * 同步整批订单，按请求顺序返回主键。
     * @param request 订单与自动排产选项
     * @return 订单主键列表
     */
    @PostMapping("/orders/sync-batch")
    public Map<String, List<Long>> syncBatch(@Valid @RequestBody BatchSyncRequest request) {
        return Map.of("ids", service.syncBatch(request));
    }

    /**
     * 对整批计划执行排产，重复请求沿用已有工序。
     * @param request 计划主键列表
     * @return 按请求顺序排列的计划主键
     */
    @PostMapping("/plans/schedule")
    public Map<String, List<Long>> schedule(@Valid @RequestBody ScheduleRequest request) {
        return Map.of("planIds", service.schedule(request));
    }

    /**
     * 检查数据库连接。
     * @return 就绪状态
     */
    @GetMapping("/health")
    public Map<String, String> health() {
        jdbc.queryForObject("SELECT 1", Integer.class);
        return Map.of("status", "UP");
    }
}
