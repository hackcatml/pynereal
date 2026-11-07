// Runtime for opt-in generated numerical functions. Included in each cache module.
#define PyInit__pynereal_native_ta PyInit__function_kernel_dependencies
#include "indicators.cpp"
#undef PyInit__pynereal_native_ta
#include <array>
#include <memory>
#include <stdexcept>

namespace nf {
static PyObject *na_bool_value;
enum Tag { F, I, B, NF, NI, NB, BIG };
struct N;
static N from_python(PyObject *p);
static PyObject *to_python(N n);
struct N {
    double f = 0;
    long long i = 0;
    Tag tag = NF;
    PyObject *big = nullptr;
    N()=default;
    N(double f,long long i,Tag tag):f(f),i(i),tag(tag){}
    N(const N &other):f(other.f),i(other.i),tag(other.tag),big(other.big){Py_XINCREF(big);}
    N(N &&other) noexcept:f(other.f),i(other.i),tag(other.tag),big(other.big){other.big=nullptr;}
    N &operator=(const N &other){if(this!=&other){Py_XINCREF(other.big);Py_XDECREF(big);f=other.f;i=other.i;tag=other.tag;big=other.big;}return *this;}
    N &operator=(N &&other) noexcept {if(this!=&other){Py_XDECREF(big);f=other.f;i=other.i;tag=other.tag;big=other.big;other.big=nullptr;}return *this;}
    ~N(){Py_XDECREF(big);}
    static N large(PyObject *value){N n;n.tag=BIG;n.big=Py_NewRef(value);return n;}
    static N real(double x) { return {x,0,F}; }
    static N integer(long long x) { return {0,x,I}; }
    static N boolean(bool x) { return {0,x,B}; }
    static N missing(Tag t=NF) { return {0,0,t}; }
    bool na() const { return tag==NF||tag==NI||tag==NB; }
    double number() const { return tag==BIG?PyLong_AsDouble(big):tag == F ? f : static_cast<double>(i); }
    bool truth() const { return !na() && (tag==BIG?PyObject_IsTrue(big):tag == F ? f != 0 : i != 0); }
};
static N owned(PyObject *p){if(!p)throw std::runtime_error("Python numerical operation failed");N n=from_python(p);Py_DECREF(p);return n;}
static N binary(N a,N b,binaryfunc operation){PyObject *x=to_python(a),*y=to_python(b);PyObject *r=operation(x,y);Py_DECREF(x);Py_DECREF(y);return owned(r);}
static N integer(__int128 x,N a,N b,binaryfunc operation) {
    if (x <= -9007199254740992LL || x >= 9007199254740992LL) return binary(a,b,operation);
    return N::integer(static_cast<long long>(x));
}
static N add(N a,N b) { if(a.na()||b.na()) return a.na()?a:b;if(a.tag==BIG||b.tag==BIG)return binary(a,b,PyNumber_Add); return a.tag==F||b.tag==F?N::real(a.number()+b.number()):integer(static_cast<__int128>(a.i)+b.i,a,b,PyNumber_Add); }
static N sub(N a,N b) { if(a.na()||b.na()) return a.na()?a:b;if(a.tag==BIG||b.tag==BIG)return binary(a,b,PyNumber_Subtract); return a.tag==F||b.tag==F?N::real(a.number()-b.number()):integer(static_cast<__int128>(a.i)-b.i,a,b,PyNumber_Subtract); }
static N mul(N a,N b) { if(a.na()||b.na()) return a.na()?a:b;if(a.tag==BIG||b.tag==BIG)return binary(a,b,PyNumber_Multiply); return a.tag==F||b.tag==F?N::real(a.number()*b.number()):integer(static_cast<__int128>(a.i)*b.i,a,b,PyNumber_Multiply); }
static N div(N a,N b) { if(a.na()||b.na()||!b.truth()) return N::missing();if(a.tag==BIG||b.tag==BIG||(a.tag!=F&&b.tag!=F))return binary(a,b,PyNumber_TrueDivide); return N::real(a.number()/b.number()); }
static N neg(N a) { if(a.na())return a;if(a.tag==F)return N::real(-a.f);return sub(N::integer(0),a); }
static N absolute(N a) { if(a.na())return N::missing();if(a.tag==BIG)return owned(PyNumber_Absolute(a.big));if(a.tag==F)return N::real(std::fabs(a.f));return a.i<0?neg(a):N::integer(a.i); }
static bool rich(N a,N b,int op){PyObject *x=to_python(a),*y=to_python(b);int result=PyObject_RichCompareBool(x,y,op);Py_DECREF(x);Py_DECREF(y);if(result<0)throw std::runtime_error("Python comparison failed");return result;}
static N minimum(N a,N b) { if(a.na()||b.na())return N::missing(); if(a.tag==BIG||b.tag==BIG)return rich(b,a,Py_LT)?b:a;return (a.tag!=F&&b.tag!=F?b.i<a.i:b.number()<a.number())?b:a; }
static N maximum(N a,N b) { if(a.na()||b.na())return N::missing(); if(a.tag==BIG||b.tag==BIG)return rich(b,a,Py_GT)?b:a;return (a.tag!=F&&b.tag!=F?b.i>a.i:b.number()>a.number())?b:a; }
static N rounded(N a) {
    if(a.na()) return N::missing();
    if(a.tag==BIG)return a;
    if(a.tag!=F) return N::integer(a.i);
    if(!std::isfinite(a.f)||std::abs(a.f)>=9007199254740992.0){PyObject *x=to_python(a);PyObject *r=PyObject_CallOneArg(builtin_round,x);Py_DECREF(x);return owned(r);}
    double base=std::floor(a.f), fraction=a.f-base;
    return N::integer(static_cast<long long>(fraction>.5||(fraction==.5&&std::fmod(base,2.0)!=0)?base+1:base));
}
static bool cmp(N a,N b,int op) {
    if(a.na()||b.na()) return false;
    if(a.tag==BIG||b.tag==BIG){
        if(op==1&&((a.tag==F&&std::isnan(a.f))||(b.tag==F&&std::isnan(b.f))))return false;
        const int ops[]={Py_EQ,Py_NE,Py_LT,Py_LE,Py_GT,Py_GE};return rich(a,b,ops[op]);
    }
    if(a.tag!=F&&b.tag!=F) {
        switch(op){case 0:return a.i==b.i;case 1:return a.i!=b.i;case 2:return a.i<b.i;case 3:return a.i<=b.i;case 4:return a.i>b.i;default:return a.i>=b.i;}
    }
    double x=a.number(),y=b.number(),d=x-y;
    if(a.tag==B||b.tag==B){switch(op){case 0:return x==y;case 1:return x!=y;case 2:return x<y;case 3:return x<=y;case 4:return x>y;default:return x>=y;}}
    switch(op){case 0:return x==y||(-1e-10<=d&&d<=1e-10);case 1:return d>1e-10||d< -1e-10;case 2:return d< -1e-10;case 3:return x<=y||d<=1e-10;case 4:return d>1e-10;default:return x>=y||d>=-1e-10;}
}
static Value value(N n) { return n.na()?Value():Value(n.number()); }
static N number(Value v) { return v.missing?N::missing():N::real(v.number); }
struct History {
    std::vector<N> rows;
    size_t size=0, write=0;
    long long last=-1;
    explicit History(int cap):rows(cap,N::missing(NI)){}
    N set(N v) { if(!size)return N::missing(NI);rows[(write+rows.size()-1)%rows.size()]=v;return v; }
    N add(N v,long long bar) { if(last==bar)return set(v);rows[write]=v;write=(write+1)%rows.size();if(size<rows.size())++size;last=bar;return v; }
    N get(int ago) const { return static_cast<size_t>(ago)>=size?N::missing(NI):rows[(write+rows.size()-1-ago)%rows.size()]; }
};
struct When {
    std::vector<N> rows;
    size_t count=0,write=0;
    explicit When(int occurrence):rows(occurrence+1){}
    N step(N condition,N source) {
        if(source.na()) return N::missing();
        if(condition.truth()){rows[write]=source;write=(write+1)%rows.size();if(count<rows.size())++count;}
        return count==rows.size()?rows[write]:N::missing();
    }
};
static PyObject *legacy_factory;
static unsigned long long legacy_steps=0;
struct Kernel {
    State fast;
    PyObject *slow=nullptr;
    Kernel(int kind,int cap):fast(kind,cap){}
    Kernel(const Kernel &other):fast(other.fast){
        if(other.slow){slow=PyObject_CallMethod(other.slow,"__copy__",nullptr);if(!slow)throw std::runtime_error("Could not clone fallback state");}
    }
    ~Kernel(){Py_XDECREF(slow);}
    N fallback(N n,int length,long long bar){
        if(!slow){
            Indicator view{};view.state=&fast;
            PyObject *state=indicator_export(&view,nullptr);
            if(!state)throw std::runtime_error("Could not export native state");
            slow=PyObject_CallOneArg(legacy_factory,state);Py_DECREF(state);
            if(!slow)throw std::runtime_error("Could not restore Python state");
        }
        ++legacy_steps;
        PyObject *source=to_python(n),*result;
        if(fast.kind<5)result=PyObject_CallMethod(slow,"step","OiLO",source,length,bar,Py_None);
        else if(fast.kind<8)result=PyObject_CallMethod(slow,"extrema_step","OiLOOO",source,length,bar,Py_False,Py_False,Py_False);
        else result=PyObject_CallMethod(slow,"statistics_step","OiLO",source,length,bar,fast.kind==8?Py_None:Py_True);
        Py_DECREF(source);
        return owned(result);
    }
};
static N ta(Kernel &kernel,N n,int length,long long bar) {
    State &s=kernel.fast;
    if(s.kind==8&&length==1)return n;
    bool valid=n.tag==F || (s.kind<4?n.na():n.tag==NF);
    if(kernel.slow||!valid)return kernel.fallback(n,length,bar);
    Value a=value(n),r;
    if(length<1||length>5000) throw std::runtime_error("Unsupported indicator length");
    if(s.kind==0)r=s.sum.sma(a,length,bar);
    else if(s.kind==8)r=s.sum.step(a,length,bar);
    else if(s.kind==1||s.kind==2)r=s.up.step(a,length,bar,s.kind==1?2.0/(length+1):1.0/length);
    else if(s.kind==4){if(!s.wma.step(a,length,bar,r))return kernel.fallback(n,length,bar);}
    else if(s.kind==5||s.kind==6){s.extrema.step(a,length,bar,s.kind==5,false);r=bar<length-1?Value():s.extrema.last;}
    else if(s.kind==9||s.kind==10){
        if(!s.variance.supported(a,length,bar))return kernel.fallback(n,length,bar);
        r=s.variance.step(a,length,bar,true);
        if(s.kind==10&&!r.missing){if(r.number<0&&r.number> -1e-9)r.number=0;if(r.number<0)throw std::runtime_error("Negative variance");r.number=std::sqrt(r.number);}
    } else if(s.kind==3&&!a.missing){
        if(!s.previous.missing){
            double u=a.number-s.previous.number,d=s.previous.number-a.number;
            Value up=s.up.step(Value(u<0?0:u),length,bar,1.0/length),down=s.down.step(Value(d<0?0:d),length,bar,1.0/length);
            if(!up.missing&&!down.missing&&down.number!=0){double den=1+up.number/down.number;if(den!=0)r=Value(100-100/den);}
        }
        s.previous=a;
    }
    return number(r);
}
struct Base {
    long long last=-1;
    virtual ~Base()=default;
    virtual std::vector<N> run(long long bar,const std::vector<N>&v)=0;
    virtual Base *clone() const=0;
};
static N from_python(PyObject *p) {
    if(PyBool_Check(p))return N::boolean(p==Py_True);
    if(PyFloat_CheckExact(p))return N::real(PyFloat_AS_DOUBLE(p));
    if(PyLong_CheckExact(p)){
        int overflow=0;long long i=PyLong_AsLongLongAndOverflow(p,&overflow);
        if(overflow||i<=-9007199254740992LL||i>=9007199254740992LL)return N::large(p);
        if(PyErr_Occurred())throw std::runtime_error("Native integer input is out of range");
        return N::integer(i);
    }
    if(Py_TYPE(p)==reinterpret_cast<PyTypeObject *>(na_type))return N::missing(p==na_int_value?NI:p==na_bool_value?NB:NF);
    throw std::runtime_error("Native function requires builtin numerical arguments");
}
static PyObject *to_python(N n) {
    if(n.tag==BIG)return Py_NewRef(n.big);
    if(n.na())return Py_NewRef(n.tag==NI?na_int_value:n.tag==NB?na_bool_value:na_value);
    if(n.tag==B)return PyBool_FromLong(n.i!=0);
    if(n.tag==I)return PyLong_FromLongLong(n.i);
    return PyFloat_FromDouble(n.f);
}
} // namespace nf

// The generated translation unit supplies a factory before including its module footer.
