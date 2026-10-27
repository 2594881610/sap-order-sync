package bench.orders;

import java.math.BigDecimal;
import javax.validation.constraints.DecimalMin;
import javax.validation.constraints.Digits;
import javax.validation.constraints.NotEmpty;
import javax.validation.constraints.NotNull;
import javax.validation.constraints.Size;

public record OrderRequest(
        @NotEmpty @Size(max = 64) String orderNo,
        @NotEmpty @Size(max = 64) String itemCode,
        @NotNull @DecimalMin(value = "0", inclusive = false)
        @Digits(integer = 14, fraction = 4) BigDecimal qty) {
}
