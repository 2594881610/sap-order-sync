package bench.orders;

import java.sql.PreparedStatement;
import java.sql.Statement;
import java.util.List;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.support.GeneratedKeyHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.annotation.Transactional;

@Service
public class OrderService {
    private final JdbcTemplate jdbc;

    /**
     * 使用同一数据源完成订单查询与写入。
     * @param jdbc 订单数据访问对象
     */
    public OrderService(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * 按外部订单号同步物料和数量，已有订单沿用原主键。
     * @param request 本次同步的订单
     * @return 持久化订单主键
     */
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public long sync(OrderRequest request) {
        List<Long> ids = jdbc.query("SELECT id FROM orders WHERE order_no = ? ORDER BY id",
                (rs, row) -> rs.getLong(1), request.orderNo());
        if (!ids.isEmpty()) {
            long id = ids.get(0);
            jdbc.update("UPDATE orders SET item_code = ?, qty = ? WHERE id = ?",
                    request.itemCode(), request.qty(), id);
            return id;
        }
        GeneratedKeyHolder keys = new GeneratedKeyHolder();
        jdbc.update(connection -> {
            PreparedStatement statement = connection.prepareStatement(
                    "INSERT INTO orders(order_no, item_code, qty) VALUES (?, ?, ?)",
                    Statement.RETURN_GENERATED_KEYS);
            statement.setString(1, request.orderNo());
            statement.setString(2, request.itemCode());
            statement.setBigDecimal(3, request.qty());
            return statement;
        }, keys);
        return keys.getKey().longValue();
    }
}
