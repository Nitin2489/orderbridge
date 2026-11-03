from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import (
    Product,
    ProductStatus,
    Retailer,
    RetailerProductPrice,
    Scheme,
    SchemeType,
    User,
    UserRole,
)


RETAILERS = [
    {
        "phone": "+919000000001",
        "name": "Sharma General Store",
        "owner": "Amit Sharma",
        "address": "Wright Town, Jabalpur",
        "credit_limit": Decimal("50000.00"),
        "outstanding": Decimal("12000.00"),
    },
    {
        "phone": "+919000000002",
        "name": "Gupta Kirana Store",
        "owner": "Rajesh Gupta",
        "address": "Napier Town, Jabalpur",
        "credit_limit": Decimal("75000.00"),
        "outstanding": Decimal("25000.00"),
    },
    {
        "phone": "+919000000003",
        "name": "Patel Provision Store",
        "owner": "Suresh Patel",
        "address": "Madan Mahal, Jabalpur",
        "credit_limit": Decimal("30000.00"),
        "outstanding": Decimal("5000.00"),
    },
]

PRODUCTS = [
    {
        "sku": "GB-075",
        "name": "Glucose Biscuit 75g",
        "brand": "Sample Foods",
        "category": "Biscuits",
        "description": "Glucose biscuits, 75g pack",
        "units_per_case": 96,
        "stock": 340,
        "pack_price": Decimal("8.10"),
    },
    {
        "sku": "DW-1000",
        "name": "Detergent Powder 1kg",
        "brand": "Sample Home Care",
        "category": "Home Care",
        "description": "Detergent powder, 1kg pack",
        "units_per_case": 20,
        "stock": 85,
        "pack_price": Decimal("101.50"),
    },
    {
        "sku": "TE-250",
        "name": "Tea 250g",
        "brand": "Sample Beverages",
        "category": "Beverages",
        "description": "Packaged tea, 250g",
        "units_per_case": 40,
        "stock": 0,
        "pack_price": Decimal("118.00"),
    },
]


def seed_users_and_retailers(session) -> list[Retailer]:
    retailers = []

    for data in RETAILERS:
        user = session.scalar(
            select(User).where(User.phone_number == data["phone"])
        )

        if user is None:
            user = User(
                phone_number=data["phone"],
                full_name=data["owner"],
                role=UserRole.RETAILER,
                is_active=True,
            )
            session.add(user)
            session.flush()

        retailer = session.scalar(
            select(Retailer).where(Retailer.user_id == user.id)
        )

        if retailer is None:
            retailer = Retailer(
                user_id=user.id,
                business_name=data["name"],
                owner_name=data["owner"],
                address=data["address"],
                credit_limit=data["credit_limit"],
                outstanding_balance=data["outstanding"],
                is_active=True,
            )
            session.add(retailer)

        retailers.append(retailer)

    order_desk = session.scalar(
        select(User).where(User.phone_number == "+919000000010")
    )

    if order_desk is None:
        session.add(
            User(
                phone_number="+919000000010",
                full_name="Order Desk Operator",
                role=UserRole.ORDER_DESK,
                is_active=True,
            )
        )

    session.flush()
    return retailers


def seed_products_and_prices(session, retailers: list[Retailer]) -> dict:
    products = {}

    for data in PRODUCTS:
        product = session.scalar(
            select(Product).where(Product.sku == data["sku"])
        )

        if product is None:
            product = Product(sku=data["sku"])
            session.add(product)

        product.name = data["name"]
        product.description = data["description"]
        product.brand = data["brand"]
        product.category = data["category"]
        product.unit_name = "case"
        product.units_per_case = data["units_per_case"]
        product.stock_quantity = data["stock"]
        product.status = ProductStatus.ACTIVE

        session.flush()
        products[data["sku"]] = product

        # Convert the source pack price into a case price.
        case_price = data["pack_price"] * data["units_per_case"]

        for retailer in retailers:
            price = session.scalar(
                select(RetailerProductPrice).where(
                    RetailerProductPrice.retailer_id == retailer.id,
                    RetailerProductPrice.product_id == product.id,
                )
            )

            if price is None:
                price = RetailerProductPrice(
                    retailer_id=retailer.id,
                    product_id=product.id,
                )
                session.add(price)

            price.unit_price = case_price
            price.is_active = True

    session.flush()
    return products


def seed_schemes(session, products: dict) -> None:
    now = datetime.now(timezone.utc)

    scheme_data = [
        {
            "sku": "GB-075",
            "name": "Buy 10 cases, get 1 free",
            "scheme_type": SchemeType.FREE_GOODS,
            "minimum_quantity": 10,
            "free_quantity": 1,
            "discount_percent": Decimal("0.00"),
        },
        {
            "sku": "DW-1000",
            "name": "3% off on 5 or more cases",
            "scheme_type": SchemeType.SLAB_DISCOUNT,
            "minimum_quantity": 5,
            "free_quantity": 0,
            "discount_percent": Decimal("3.00"),
        },
    ]

    for data in scheme_data:
        product = products[data["sku"]]

        scheme = session.scalar(
            select(Scheme).where(
                Scheme.product_id == product.id,
                Scheme.name == data["name"],
            )
        )

        if scheme is None:
            scheme = Scheme(
                product_id=product.id,
                name=data["name"],
                starts_at=now,
            )
            session.add(scheme)

        scheme.scheme_type = data["scheme_type"]
        scheme.minimum_quantity = data["minimum_quantity"]
        scheme.free_quantity = data["free_quantity"]
        scheme.discount_percent = data["discount_percent"]
        scheme.starts_at = now
        scheme.ends_at = now + timedelta(days=365)
        scheme.is_active = True


def seed_database() -> None:
    with SessionLocal.begin() as session:
        retailers = seed_users_and_retailers(session)
        products = seed_products_and_prices(session, retailers)
        seed_schemes(session, products)

    print("OrderBridge sample data seeded successfully.")
    print("Retailers: 3")
    print("Products: 3")
    print("Order desk users: 1")
    print("Schemes: 2")


if __name__ == "__main__":
    seed_database()
