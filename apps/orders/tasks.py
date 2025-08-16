from celery import Celery
from django.core.mail import send_mail
from django.conf import settings
from django.contrib.auth import get_user_model
from apps.orders.models import Order, OrderItem
from apps.products.models import Product
import time
import logging
import json
import requests
from datetime import datetime, timedelta  # ✅ imported once for reuse
from celery import app

User = get_user_model()


@app.task(bind=True, max_retries=3, default_retry_delay=60)
def send_order_confirmation_email(self, order_id):
    try:
        order = Order.objects.get(id=order_id)
        customer = order.customer

        subject = f'Order Confirmation - {order.order_number}'
        message = f"""
        Dear {customer.get_full_name()},

        Thank you for your order! Your order has been confirmed.

        Order Details:
        Order Number: {order.order_number}
        Total Amount: ${order.total_amount}
        Status: {order.status}

        We will notify you when your order ships.

        Best regards,
        Summit Market Team
        """

        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[customer.email],
            fail_silently=False,
        )

        return f"Order confirmation email sent to {customer.email}"
    except Exception as e:
        raise self.retry(exc=e)


@app.task(bind=True, max_retries=5, default_retry_delay=30)
def update_product_stock(self, product_id, quantity):
    try:
        product = Product.objects.get(id=product_id)
        product.stock_quantity = max(0, product.stock_quantity - quantity)  # ✅ prevent negative stock
        product.save()

        if product.stock_quantity <= 0:
            send_low_stock_notification.delay(product_id)

        return f"Stock updated for product {product.name}"
    except Exception as e:
        raise self.retry(exc=e)


@app.task(bind=True, max_retries=3, default_retry_delay=120)
def send_low_stock_notification(self, product_id):
    try:
        product = Product.objects.get(id=product_id)
        vendor = product.vendor

        subject = f'Low Stock Alert - {product.name}'
        message = f"""
        Dear {vendor.get_full_name()},

        Your product "{product.name}" is running low on stock.
        Current stock: {product.stock_quantity}

        Please restock soon to avoid out-of-stock situations.

        Best regards,
        Summit Market Team
        """

        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[vendor.email],
            fail_silently=False,
        )

        return f"Low stock notification sent to {vendor.email}"
    except Exception as e:
        raise self.retry(exc=e)


@app.task(bind=True, max_retries=3, default_retry_delay=60)
def process_order_items(self, order_id):
    try:
        order = Order.objects.get(id=order_id)
        items = order.items.all()  # ✅ ensure related_name="items" is set in OrderItem

        for item in items:
            update_product_stock.delay(item.product.id, item.quantity)
            # ❌ avoid blocking worker with time.sleep — use countdown instead
            # update_product_stock.apply_async((item.product.id, item.quantity), countdown=i)

        send_order_confirmation_email.delay(order_id)

        return f"Order {order_id} processed successfully"
    except Exception as e:
        raise self.retry(exc=e)


@app.task(bind=True, max_retries=2, default_retry_delay=300)
def generate_daily_report(self):
    try:
        yesterday = datetime.now().date() - timedelta(days=1)

        orders = Order.objects.filter(created_at__date=yesterday)
        total_orders = orders.count()
        total_revenue = sum(order.total_amount for order in orders)

        report_data = {
            'date': yesterday.isoformat(),
            'total_orders': total_orders,
            'total_revenue': str(total_revenue),
            'average_order_value': str(total_revenue / total_orders if total_orders > 0 else 0)
        }

        with open(f'daily_report_{yesterday}.json', 'w') as f:
            json.dump(report_data, f)

        return f"Daily report generated for {yesterday}"
    except Exception as e:
        raise self.retry(exc=e)


@app.task(bind=True, max_retries=3, default_retry_delay=600)
def sync_external_inventory(self, vendor_id):
    try:
        vendor = User.objects.get(id=vendor_id)
        products = Product.objects.filter(vendor=vendor)

        for product in products:
            response = requests.get(f'https://api.external-inventory.com/product/{product.sku}', timeout=5)  # ✅ timeout added
            if response.status_code == 200:
                data = response.json()
                product.stock_quantity = data.get('stock', product.stock_quantity)
                product.price = data.get('price', product.price)
                product.save()
                time.sleep(0.5)

        return f"Inventory synced for vendor {vendor.get_full_name()}"
    except Exception as e:
        raise self.retry(exc=e)


@app.task(bind=True, max_retries=2, default_retry_delay=3600)
def cleanup_old_orders(self, days=30):
    try:
        cutoff_date = datetime.now() - timedelta(days=days)
        old_orders = Order.objects.filter(
            created_at__lt=cutoff_date,
            status__in=['delivered', 'cancelled']
        )

        count = old_orders.count()
        old_orders.delete()

        return f"Cleaned up {count} old orders"
    except Exception as e:
        raise self.retry(exc=e)


@app.task(bind=True, max_retries=2, default_retry_delay=7200)
def backup_database(self):
    try:
        import subprocess
        import os

        backup_dir = 'backups'
        os.makedirs(backup_dir, exist_ok=True)  # ✅ simplified

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_file = f'{backup_dir}/backup_{timestamp}.json'  # ✅ dumpdata creates JSON not SQL

        subprocess.run([
            'python', 'manage.py', 'dumpdata',
            '--exclude', 'contenttypes',
            '--exclude', 'auth.Permission',
            '--indent', '2'
        ], check=True, stdout=open(backup_file, 'w'))

        return f"Database backup created: {backup_file}"
    except Exception as e:
        raise self.retry(exc=e)