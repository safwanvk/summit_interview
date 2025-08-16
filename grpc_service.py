import os
import time
from decimal import Decimal, ROUND_HALF_UP

import grpc
from concurrent import futures
from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
import django

# ---- Django setup (required when running outside manage.py) ----
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ecommerce.settings")
django.setup()

from django.contrib.auth import get_user_model
from apps.products.models import Product
from apps.orders.models import Order, OrderItem, ShippingAddress

# Import the generated gRPC code
import summit_market_pb2
import summit_market_pb2_grpc

User = get_user_model()


def D(val) -> Decimal:
    """Safe Decimal constructor for numbers/strings."""
    if isinstance(val, Decimal):
        return val
    return Decimal(str(val))


def money(val: Decimal) -> str:
    """Return a string with 2 dp, rounded half up, for protobuf string fields."""
    return str(val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class SummitMarketService(summit_market_pb2_grpc.SummitMarketServiceServicer):

    # ---------------- Users ----------------
    def GetUser(self, request, context):
        try:
            user = User.objects.get(id=request.user_id)
            return summit_market_pb2.UserResponse(
                user_id=user.id,
                username=user.username,
                email=user.email or "",
                first_name=user.first_name or "",
                last_name=user.last_name or "",
                is_active=bool(user.is_active),
            )
        except User.DoesNotExist:
            context.abort(grpc.StatusCode.NOT_FOUND, "User not found")
        except Exception as e:
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def CreateUser(self, request, context):
        try:
            # Minimal validation — consider adding stronger checks/unique email handling
            user = User.objects.create_user(
                username=request.username,
                email=request.email,
                password=request.password,
                first_name=request.first_name,
                last_name=request.last_name,
            )
            return summit_market_pb2.UserResponse(
                user_id=user.id,
                username=user.username,
                email=user.email or "",
                first_name=user.first_name or "",
                last_name=user.last_name or "",
                is_active=bool(user.is_active),
            )
        except Exception as e:
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    # ---------------- Products ----------------
    def GetProduct(self, request, context):
        try:
            product = Product.objects.select_related("vendor").get(id=request.product_id)
            vendor_id = product.vendor.id if getattr(product, "vendor", None) else 0
            price = product.price if product.price is not None else Decimal("0")
            return summit_market_pb2.ProductResponse(
                product_id=product.id,
                name=product.name or "",
                description=product.description or "",
                price=money(D(price)),
                stock_quantity=int(product.stock_quantity or 0),
                vendor_id=int(vendor_id),
            )
        except Product.DoesNotExist:
            context.abort(grpc.StatusCode.NOT_FOUND, "Product not found")
        except Exception as e:
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def CreateProduct(self, request, context):
        try:
            vendor = None
            if request.vendor_id:
                try:
                    vendor = User.objects.get(id=request.vendor_id)
                except User.DoesNotExist:
                    context.abort(grpc.StatusCode.NOT_FOUND, "Vendor not found")

            # Ensure price is Decimal
            price = D(request.price) if request.price else Decimal("0")

            with transaction.atomic():
                product = Product.objects.create(
                    name=request.name,
                    description=request.description,
                    price=price,
                    vendor=vendor,
                    stock_quantity=int(request.stock_quantity or 0),
                )

            vendor_id = product.vendor.id if product.vendor else 0
            return summit_market_pb2.ProductResponse(
                product_id=product.id,
                name=product.name or "",
                description=product.description or "",
                price=money(D(product.price or 0)),
                stock_quantity=int(product.stock_quantity or 0),
                vendor_id=int(vendor_id),
            )
        except grpc.RpcError:
            raise
        except Exception as e:
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    # ---------------- Orders ----------------
    def GetOrder(self, request, context):
        try:
            order = (
                Order.objects.select_related("customer")
                .prefetch_related("items")
                .get(id=request.order_id)
            )
            return summit_market_pb2.OrderResponse(
                order_id=order.id,
                customer_id=order.customer.id if order.customer_id else 0,
                status=str(order.status),
                total_amount=money(D(order.total_amount or 0)),
                created_at=order.created_at.isoformat() if order.created_at else "",
            )
        except Order.DoesNotExist:
            context.abort(grpc.StatusCode.NOT_FOUND, "Order not found")
        except Exception as e:
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def CreateOrder(self, request, context):
        """
        Assumptions:
          - request.customer_id: int
          - request.shipping_address_id / billing_address_id: int (FK IDs)
          - request.items: repeated message with fields: product_id (int), quantity (int)
          - tax = 10% of subtotal, shipping = 10.00 fixed (adapt to your logic)
        """
        try:
            try:
                customer = User.objects.get(id=request.customer_id)
            except User.DoesNotExist:
                context.abort(grpc.StatusCode.NOT_FOUND, "Customer not found")

            shipping_addr = None
            billing_addr = None
            if getattr(request, "shipping_address_id", 0):
                try:
                    shipping_addr = ShippingAddress.objects.get(id=request.shipping_address_id)
                except ShippingAddress.DoesNotExist:
                    context.abort(grpc.StatusCode.NOT_FOUND, "Shipping address not found")
            if getattr(request, "billing_address_id", 0):
                try:
                    billing_addr = ShippingAddress.objects.get(id=request.billing_address_id)
                except ShippingAddress.DoesNotExist:
                    context.abort(grpc.StatusCode.NOT_FOUND, "Billing address not found")

            with transaction.atomic():
                order = Order.objects.create(
                    customer=customer,
                    shipping_address=shipping_addr,
                    billing_address=billing_addr,
                    subtotal=Decimal("0"),
                    tax_amount=Decimal("0"),
                    shipping_cost=Decimal("0"),
                    total_amount=Decimal("0"),
                    # status default assumed by model (e.g., "pending")
                )

                subtotal = Decimal("0")

                # Create items and validate stock
                for item in request.items:
                    try:
                        product = Product.objects.select_for_update().get(id=item.product_id)
                    except Product.DoesNotExist:
                        context.abort(grpc.StatusCode.NOT_FOUND, f"Product {item.product_id} not found")

                    qty = int(item.quantity or 0)
                    if qty <= 0:
                        context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Quantity must be > 0")

                    if product.stock_quantity is not None and product.stock_quantity < qty:
                        context.abort(grpc.StatusCode.FAILED_PRECONDITION, f"Insufficient stock for product {product.id}")

                    unit_price = D(product.price or 0)
                    line_total = unit_price * D(qty)

                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        quantity=qty,
                        unit_price=unit_price,
                        total_price=line_total,
                    )

                    # Decrement stock
                    if product.stock_quantity is not None:
                        product.stock_quantity = product.stock_quantity - qty
                        product.save(update_fields=["stock_quantity"])

                    subtotal += line_total

                tax = (subtotal * D("0.10")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)  # 10% example
                shipping = D("10.00") if subtotal > 0 else D("0.00")  # example logic

                total = (subtotal + tax + shipping).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                order.subtotal = subtotal
                order.tax_amount = tax
                order.shipping_cost = shipping
                order.total_amount = total
                order.save(update_fields=["subtotal", "tax_amount", "shipping_cost", "total_amount"])

            return summit_market_pb2.OrderResponse(
                order_id=order.id,
                customer_id=order.customer.id if order.customer_id else 0,
                status=str(order.status),
                total_amount=money(D(order.total_amount or 0)),
                created_at=order.created_at.isoformat() if order.created_at else "",
            )
        except grpc.RpcError:
            raise
        except Exception as e:
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    # ---------------- Stats ----------------
    def GetUserStats(self, request, context):
        try:
            total_users = User.objects.count()
            active_users = User.objects.filter(is_active=True).count()
            return summit_market_pb2.UserStatsResponse(
                total_users=int(total_users),
                active_users=int(active_users),
                inactive_users=int(total_users - active_users),
            )
        except Exception as e:
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    def GetOrderStats(self, request, context):
        try:
            delivered_qs = Order.objects.filter(status="delivered")
            total_orders = Order.objects.count()
            delivered_count = delivered_qs.count()
            total_revenue = delivered_qs.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")

            return summit_market_pb2.OrderStatsResponse(
                total_orders=int(total_orders),
                total_revenue=money(D(total_revenue)),
                average_order_value=money(D(total_revenue) / D(delivered_count) if delivered_count > 0 else D("0")),
            )
        except Exception as e:
            context.abort(grpc.StatusCode.INTERNAL, str(e))


def serve():
    # Consider TLS + auth (e.g., tokens in metadata) for production
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    summit_market_pb2_grpc.add_SummitMarketServiceServicer_to_server(SummitMarketService(), server)
    server.add_insecure_port("[::]:50051")
    server.start()
    print("gRPC server started on port 50051")
    try:
        while True:
            time.sleep(86400)
    except KeyboardInterrupt:
        server.stop(0)


if __name__ == "__main__":
    serve()